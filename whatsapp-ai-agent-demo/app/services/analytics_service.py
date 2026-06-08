# ==========================================================
# FILE: app/services/analytics_service.py (ENTERPRISE v23.0 - COMPLETE UPGRADE)
# ==========================================================
# ENTERPRISE ANALYTICS SERVICE v23.0:
# - PHASE 1: Dealer Search + Complete Dealer Dashboard + Ranking Engine
# - PHASE 2: Product Intelligence + Ranking Engine
# - PHASE 3: City Intelligence Dashboard
# - PHASE 4: Warehouse Intelligence Dashboard
# - PHASE 5: Executive Dashboard + Management Insights
# - PHASE 6: Exception Dashboard
# - PHASE 7: WhatsApp Formatted Responses + Cache Layers
# ==========================================================

import json
import hashlib
from datetime import date, datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple
from functools import lru_cache
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, case, desc, distinct, or_, text
from loguru import logger

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("rapidfuzz not available. Install with: pip install rapidfuzz")

from app.models import DeliveryReport
from app.services.business_rules_service import BusinessRulesService


class AnalyticsService:
    """Enterprise Analytics Service v23.0 - Complete Upgrade"""

    def __init__(self, db: Session, groq_api_key: str = None, redis_url: str = None):
        self.db = db
        self.rules = BusinessRulesService()
        
        # ==================================================
        # CACHE LAYERS (Phase 7)
        # ==================================================
        self._cache = {}
        self._dealer_cache = {}
        self._product_cache = {}
        self._city_cache = {}
        self._warehouse_cache = {}
        self._cache_ttl = {
            "dealer": 300,      # 5 minutes
            "product": 600,     # 10 minutes
            "city": 600,        # 10 minutes
            "warehouse": 600,   # 10 minutes
            "executive": 120,   # 2 minutes
            "exception": 60     # 1 minute
        }
        
        logger.info("✅ Analytics Service v23.0 initialized")

    # ==================================================
    # PHASE 1: DEALER ANALYTICS (Complete)
    # ==================================================

    def search_dealer(self, dealer_name: str, threshold: int = 70) -> Dict[str, Any]:
        """
        Search for dealer with fuzzy matching.
        Supports queries like: "Azam", "Afzal", "Bismillah", "Bhatti Electronics"
        """
        cache_key = f"dealer_search:{dealer_name}:{threshold}"
        
        # Check cache
        if cache_key in self._dealer_cache:
            cached_time, cached_value = self._dealer_cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["dealer"]:
                return cached_value
        
        try:
            # Get all dealers
            dealers = self.db.query(DeliveryReport.customer_name).distinct().filter(
                DeliveryReport.customer_name.isnot(None)
            ).limit(1000).all()
            
            dealer_list = [d[0] for d in dealers if d[0]]
            
            if not dealer_list:
                return {"success": False, "error": "No dealers found"}
            
            # Exact match (case insensitive)
            dealer_lower = dealer_name.lower()
            for d in dealer_list:
                if d.lower() == dealer_lower:
                    result = {
                        "success": True,
                        "dealer": d,
                        "confidence": 100,
                        "match_type": "exact"
                    }
                    self._dealer_cache[cache_key] = (datetime.utcnow(), result)
                    return result
            
            # Fuzzy matching if rapidfuzz available
            if RAPIDFUZZ_AVAILABLE:
                matches = process.extract(
                    dealer_name, 
                    dealer_list, 
                    scorer=fuzz.WRatio,
                    limit=5
                )
                
                best_match = matches[0] if matches else None
                if best_match and best_match[1] >= threshold:
                    result = {
                        "success": True,
                        "dealer": best_match[0],
                        "confidence": best_match[1],
                        "match_type": "fuzzy",
                        "alternatives": [{"dealer": m[0], "confidence": m[1]} for m in matches[1:3]]
                    }
                    self._dealer_cache[cache_key] = (datetime.utcnow(), result)
                    return result
            
            # Contains match fallback
            for d in dealer_list:
                if dealer_lower in d.lower() or d.lower() in dealer_lower:
                    result = {
                        "success": True,
                        "dealer": d,
                        "confidence": 80,
                        "match_type": "contains"
                    }
                    self._dealer_cache[cache_key] = (datetime.utcnow(), result)
                    return result
            
            return {"success": False, "error": f"Dealer '{dealer_name}' not found"}
            
        except Exception as e:
            logger.error(f"Dealer search error: {e}")
            return {"success": False, "error": str(e)}

    def get_complete_dealer_dashboard(self, dealer_name: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Complete dealer dashboard - Returns ALL dealer metrics.
        Supports: "Show dealer summary", "Dealer sales", "Dealer pending POD", "Dealer performance"
        """
        cache_key = f"complete_dealer_dashboard:{dealer_name}"
        
        if not force_refresh and cache_key in self._dealer_cache:
            cached_time, cached_value = self._dealer_cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["dealer"]:
                return cached_value
        
        # First, find the dealer
        search_result = self.search_dealer(dealer_name)
        if not search_result.get("success"):
            return search_result
        
        actual_dealer = search_result["dealer"]
        
        try:
            # Get all DNs for this dealer
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name == actual_dealer
            ).all()
            
            if not records:
                return {"success": False, "error": f"No records found for dealer '{actual_dealer}'"}
            
            # Calculate metrics
            total_dns = len(set(r.dn_no for r in records))
            total_value = sum(float(r.dn_amount or 0) for r in records)
            total_quantity = sum(float(r.dn_qty or 0) for r in records)
            
            # Pending metrics
            pending_dns = len([r for r in records if r.pgi_status != "Completed"])
            pending_value = sum(float(r.dn_amount or 0) for r in records if r.pgi_status != "Completed")
            pending_quantity = sum(float(r.dn_qty or 0) for r in records if r.pgi_status != "Completed")
            
            # POD metrics
            pod_pending_dns = len([r for r in records if r.pgi_status == "Completed" and r.pod_status != "Received"])
            pod_pending_value = sum(float(r.dn_amount or 0) for r in records if r.pgi_status == "Completed" and r.pod_status != "Received")
            
            # Completed metrics
            completed_dns = total_dns - pending_dns
            completed_value = total_value - pending_value
            completion_rate = (completed_dns / total_dns * 100) if total_dns else 0
            
            # Delivery time analysis
            delivery_times = []
            for r in records:
                if r.good_issue_date and r.delivery_date:
                    delivery_time = (r.delivery_date - r.good_issue_date).days
                    if delivery_time >= 0:
                        delivery_times.append(delivery_time)
            avg_delivery_time = sum(delivery_times) / len(delivery_times) if delivery_times else 0
            
            # Last order date
            last_order_date = max((r.dn_create_date for r in records if r.dn_create_date), default=None)
            
            # Risk score calculation
            risk_score = min(100, 
                (pending_dns / total_dns * 40) if total_dns else 0 +
                (pod_pending_dns / total_dns * 30) if total_dns else 0 +
                (avg_delivery_time / 7 * 30) if avg_delivery_time else 0
            )
            
            if risk_score >= 70:
                risk_level = "Critical"
                risk_icon = "💀"
            elif risk_score >= 50:
                risk_level = "High"
                risk_icon = "🚨"
            elif risk_score >= 30:
                risk_level = "Medium"
                risk_icon = "⚠️"
            else:
                risk_level = "Low"
                risk_icon = "✅"
            
            # Get city and sales manager
            city = records[0].ship_to_city if records else "Unknown"
            sales_manager = self._get_sales_manager_for_city(city)
            
            result = {
                "success": True,
                "dealer": actual_dealer,
                "city": city,
                "sales_manager": sales_manager,
                "total_dns": total_dns,
                "total_value": total_value,
                "total_quantity": total_quantity,
                "pending_dns": pending_dns,
                "pending_value": pending_value,
                "pending_quantity": pending_quantity,
                "pod_pending_dns": pod_pending_dns,
                "pod_pending_value": pod_pending_value,
                "completed_dns": completed_dns,
                "completed_value": completed_value,
                "completion_rate": round(completion_rate, 1),
                "avg_delivery_time": round(avg_delivery_time, 1),
                "last_order_date": last_order_date,
                "risk_score": round(risk_score, 1),
                "risk_level": risk_level,
                "risk_icon": risk_icon,
                "formatted_message": self._format_dealer_dashboard(
                    actual_dealer, city, sales_manager, total_value, total_quantity,
                    total_dns, pending_dns, pod_pending_dns, completion_rate,
                    avg_delivery_time, risk_level, risk_icon, last_order_date
                )
            }
            
            # Cache result
            self._dealer_cache[cache_key] = (datetime.utcnow(), result)
            return result
            
        except Exception as e:
            logger.error(f"Complete dealer dashboard error: {e}")
            return {"success": False, "error": str(e)}

    def _format_dealer_dashboard(self, dealer: str, city: str, manager: str, 
                                  value: float, qty: float, total_dns: int, 
                                  pending_dns: int, pod_pending: int, 
                                  completion_rate: float, avg_delivery: float,
                                  risk_level: str, risk_icon: str, last_order: date) -> str:
        """Format dealer dashboard for WhatsApp"""
        return f"""
🏪 *DEALER DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 *BASIC INFO*
• Name: {dealer}
• City: {city}
• Manager: {manager}

💰 *FINANCIALS*
• Total Value: Rs {value:,.2f}
• Total Qty: {qty:,.0f}
• DNs: {total_dns}

⚠️ *PENDING*
• Pending DNs: {pending_dns}
• POD Pending: {pod_pending}
• Completion: {completion_rate}%

⏱️ *PERFORMANCE*
• Avg Delivery: {avg_delivery} days
• Last Order: {last_order.strftime('%d-%b-%Y') if last_order else 'N/A'}

{risk_icon} *RISK: {risk_level}*

💡 Type "Dealer performance" for detailed analysis
"""

    def top_dealers(self, limit: int = 10, by: str = "value") -> List[Dict]:
        """Get top dealers by value, quantity, or growth"""
        cache_key = f"top_dealers:{limit}:{by}"
        
        if cache_key in self._dealer_cache:
            cached_time, cached_value = self._dealer_cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["dealer"]:
                return cached_value
        
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.sum(DeliveryReport.dn_qty).label("total_qty"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).all()
            
            dealers = []
            for r in results:
                if not r.customer_name:
                    continue
                dealers.append({
                    "dealer": r.customer_name,
                    "value": float(r.total_value or 0),
                    "quantity": float(r.total_qty or 0),
                    "dns": r.total_dns
                })
            
            if by == "value":
                dealers.sort(key=lambda x: x["value"], reverse=True)
            elif by == "quantity":
                dealers.sort(key=lambda x: x["quantity"], reverse=True)
            elif by == "dns":
                dealers.sort(key=lambda x: x["dns"], reverse=True)
            
            result = dealers[:limit]
            self._dealer_cache[cache_key] = (datetime.utcnow(), result)
            return result
            
        except Exception as e:
            logger.error(f"Top dealers error: {e}")
            return []

    def bottom_dealers(self, limit: int = 10) -> List[Dict]:
        """Get bottom performing dealers"""
        all_dealers = self.top_dealers(1000, "value")
        return all_dealers[-limit:] if len(all_dealers) > limit else all_dealers

    def high_risk_dealers(self, limit: int = 10) -> List[Dict]:
        """Get dealers with highest risk score"""
        cache_key = f"high_risk_dealers:{limit}"
        
        if cache_key in self._dealer_cache:
            cached_time, cached_value = self._dealer_cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["dealer"]:
                return cached_value
        
        try:
            # Get all dealers and calculate risk
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(distinct(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                    else_=None
                ))).label("pending_dns")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).all()
            
            dealers = []
            for r in results:
                if not r.customer_name or r.total_dns == 0:
                    continue
                
                pending_pct = (r.pending_dns / r.total_dns) * 100
                risk_score = min(100, pending_pct * 1.5)
                
                dealers.append({
                    "dealer": r.customer_name,
                    "risk_score": round(risk_score, 1),
                    "pending_dns": r.pending_dns,
                    "pending_value": float(r.total_value or 0) * (r.pending_dns / r.total_dns)
                })
            
            dealers.sort(key=lambda x: x["risk_score"], reverse=True)
            result = dealers[:limit]
            self._dealer_cache[cache_key] = (datetime.utcnow(), result)
            return result
            
        except Exception as e:
            logger.error(f"High risk dealers error: {e}")
            return []

    def inactive_dealers(self, days: int = 30, limit: int = 20) -> List[Dict]:
        """Get dealers with no activity in last N days"""
        cache_key = f"inactive_dealers:{days}:{limit}"
        
        if cache_key in self._dealer_cache:
            cached_time, cached_value = self._dealer_cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["dealer"]:
                return cached_value
        
        try:
            cutoff_date = date.today() - timedelta(days=days)
            
            active_dealers = self.db.query(
                DeliveryReport.customer_name,
                func.max(DeliveryReport.dn_create_date).label("last_order")
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.dn_create_date >= cutoff_date
            ).group_by(
                DeliveryReport.customer_name
            ).all()
            
            active_names = set(d.customer_name for d in active_dealers)
            
            all_dealers = self.db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label("total_value")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).all()
            
            inactive = []
            for d in all_dealers:
                if d.customer_name and d.customer_name not in active_names:
                    inactive.append({
                        "dealer": d.customer_name,
                        "last_value": float(d.total_value or 0),
                        "inactive_days": days
                    })
            
            inactive.sort(key=lambda x: x["last_value"], reverse=True)
            result = inactive[:limit]
            self._dealer_cache[cache_key] = (datetime.utcnow(), result)
            return result
            
        except Exception as e:
            logger.error(f"Inactive dealers error: {e}")
            return []

    # ==================================================
    # PHASE 2: PRODUCT ANALYTICS (Complete)
    # ==================================================

    def product_intelligence(self, product_code: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Complete product intelligence dashboard.
        Supports: "Sales of HSU-18HFPAA", "Top product", "Slow moving product"
        """
        cache_key = f"product_intelligence:{product_code}"
        
        if not force_refresh and cache_key in self._product_cache:
            cached_time, cached_value = self._product_cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["product"]:
                return cached_value
        
        try:
            # Find product
            product = self._find_product(product_code)
            if not product:
                return {"success": False, "error": f"Product '{product_code}' not found"}
            
            # Get all records for this product
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.product == product
            ).all()
            
            if not records:
                return {"success": False, "error": f"No records found for product '{product}'"}
            
            # Calculate metrics
            total_qty = sum(float(r.dn_qty or 0) for r in records)
            total_value = sum(float(r.dn_amount or 0) for r in records)
            total_dns = len(set(r.dn_no for r in records))
            
            # Completed metrics
            delivered_qty = sum(float(r.dn_qty or 0) for r in records if r.pgi_status == "Completed")
            delivered_value = sum(float(r.dn_amount or 0) for r in records if r.pgi_status == "Completed")
            fill_rate = (delivered_qty / total_qty * 100) if total_qty else 0
            
            # Pending metrics
            pending_qty = total_qty - delivered_qty
            pending_value = total_value - delivered_value
            
            # Top cities for this product
            city_sales = defaultdict(float)
            for r in records:
                if r.ship_to_city:
                    city_sales[r.ship_to_city] += float(r.dn_amount or 0)
            top_cities = sorted(city_sales.items(), key=lambda x: x[1], reverse=True)[:5]
            
            # Top dealers for this product
            dealer_sales = defaultdict(float)
            for r in records:
                if r.customer_name:
                    dealer_sales[r.customer_name] += float(r.dn_amount or 0)
            top_dealers = sorted(dealer_sales.items(), key=lambda x: x[1], reverse=True)[:5]
            
            # Top warehouses for this product
            warehouse_sales = defaultdict(float)
            for r in records:
                if r.warehouse:
                    warehouse_sales[r.warehouse] += float(r.dn_amount or 0)
            top_warehouses = sorted(warehouse_sales.items(), key=lambda x: x[1], reverse=True)[:5]
            
            # Growth calculation (compare last 30 days vs previous 30 days)
            today = date.today()
            last_30_start = today - timedelta(days=30)
            last_30_end = today
            prev_30_start = today - timedelta(days=60)
            prev_30_end = today - timedelta(days=31)
            
            last_30_value = sum(float(r.dn_amount or 0) for r in records 
                               if r.dn_create_date and last_30_start <= r.dn_create_date <= last_30_end)
            prev_30_value = sum(float(r.dn_amount or 0) for r in records 
                               if r.dn_create_date and prev_30_start <= r.dn_create_date <= prev_30_end)
            
            growth = ((last_30_value - prev_30_value) / prev_30_value * 100) if prev_30_value else 0
            
            # Risk assessment
            if fill_rate < 50:
                risk_level = "Critical"
                risk_icon = "💀"
            elif fill_rate < 70:
                risk_level = "High"
                risk_icon = "🚨"
            elif fill_rate < 85:
                risk_level = "Medium"
                risk_icon = "⚠️"
            else:
                risk_level = "Low"
                risk_icon = "✅"
            
            result = {
                "success": True,
                "product": product,
                "total_qty": total_qty,
                "total_value": total_value,
                "total_dns": total_dns,
                "delivered_qty": delivered_qty,
                "delivered_value": delivered_value,
                "pending_qty": pending_qty,
                "pending_value": pending_value,
                "fill_rate": round(fill_rate, 1),
                "growth": round(growth, 1),
                "risk_level": risk_level,
                "risk_icon": risk_icon,
                "top_cities": [{"city": c, "value": v} for c, v in top_cities],
                "top_dealers": [{"dealer": d, "value": v} for d, v in top_dealers],
                "top_warehouses": [{"warehouse": w, "value": v} for w, v in top_warehouses],
                "formatted_message": self._format_product_intelligence(
                    product, total_value, total_qty, total_dns, fill_rate, 
                    growth, risk_level, risk_icon, top_cities, top_dealers
                )
            }
            
            self._product_cache[cache_key] = (datetime.utcnow(), result)
            return result
            
        except Exception as e:
            logger.error(f"Product intelligence error: {e}")
            return {"success": False, "error": str(e)}

    def _format_product_intelligence(self, product: str, value: float, qty: float, 
                                       dns: int, fill_rate: float, growth: float,
                                       risk_level: str, risk_icon: str, 
                                       top_cities: List, top_dealers: List) -> str:
        """Format product intelligence for WhatsApp"""
        growth_icon = "📈" if growth > 0 else "📉" if growth < 0 else "➡️"
        
        message = f"""
📦 *PRODUCT INTELLIGENCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 *PRODUCT: {product}*

💰 *FINANCIALS*
• Value: Rs {value:,.2f}
• Quantity: {qty:,.0f}
• DNs: {dns}

📊 *PERFORMANCE*
• Fill Rate: {fill_rate}%
• Growth: {growth_icon} {abs(growth)}%

{risk_icon} *RISK: {risk_level}*

🌆 *TOP CITIES*
"""
        for city, val in top_cities[:3]:
            message += f"• {city}: Rs {val:,.2f}\n"
        
        message += f"\n🏪 *TOP DEALERS*\n"
        for dealer, val in top_dealers[:3]:
            message += f"• {dealer[:20]}: Rs {val:,.2f}\n"
        
        return message

    def top_products(self, limit: int = 10, by: str = "value") -> List[Dict]:
        """Get top products by value or quantity"""
        cache_key = f"top_products:{limit}:{by}"
        
        if cache_key in self._product_cache:
            cached_time, cached_value = self._product_cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["product"]:
                return cached_value
        
        try:
            results = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.sum(DeliveryReport.dn_qty).label("total_qty"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns")
            ).filter(
                DeliveryReport.product.isnot(None)
            ).group_by(
                DeliveryReport.product
            ).all()
            
            products = []
            for r in results:
                if not r.product:
                    continue
                products.append({
                    "product": r.product,
                    "value": float(r.total_value or 0),
                    "quantity": float(r.total_qty or 0),
                    "dns": r.total_dns
                })
            
            if by == "value":
                products.sort(key=lambda x: x["value"], reverse=True)
            else:
                products.sort(key=lambda x: x["quantity"], reverse=True)
            
            return products[:limit]
            
        except Exception as e:
            logger.error(f"Top products error: {e}")
            return []

    def bottom_products(self, limit: int = 10) -> List[Dict]:
        """Get bottom performing products"""
        all_products = self.top_products(1000, "value")
        return all_products[-limit:] if len(all_products) > limit else all_products

    def fast_moving_products(self, limit: int = 10) -> List[Dict]:
        """Get products with highest turnover (quantity per DN)"""
        cache_key = f"fast_moving:{limit}"
        
        if cache_key in self._product_cache:
            cached_time, cached_value = self._product_cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["product"]:
                return cached_value
        
        try:
            results = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).label("total_qty"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns")
            ).filter(
                DeliveryReport.product.isnot(None)
            ).group_by(
                DeliveryReport.product
            ).having(
                func.count(distinct(DeliveryReport.dn_no)) > 0
            ).all()
            
            products = []
            for r in results:
                if not r.product or r.total_dns == 0:
                    continue
                avg_per_dn = r.total_qty / r.total_dns
                products.append({
                    "product": r.product,
                    "avg_qty_per_dn": round(avg_per_dn, 1),
                    "total_qty": float(r.total_qty or 0),
                    "total_dns": r.total_dns
                })
            
            products.sort(key=lambda x: x["avg_qty_per_dn"], reverse=True)
            return products[:limit]
            
        except Exception as e:
            logger.error(f"Fast moving products error: {e}")
            return []

    def slow_moving_products(self, limit: int = 10) -> List[Dict]:
        """Get products with lowest turnover"""
        all_products = self.fast_moving_products(1000)
        return all_products[-limit:] if len(all_products) > limit else all_products

    def dead_stock_products(self, days: int = 90, limit: int = 20) -> List[Dict]:
        """Get products with no activity in last N days"""
        cache_key = f"dead_stock:{days}:{limit}"
        
        if cache_key in self._product_cache:
            cached_time, cached_value = self._product_cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["product"]:
                return cached_value
        
        try:
            cutoff_date = date.today() - timedelta(days=days)
            
            active_products = self.db.query(
                DeliveryReport.product
            ).filter(
                DeliveryReport.product.isnot(None),
                DeliveryReport.dn_create_date >= cutoff_date
            ).distinct().all()
            
            active_set = set(p[0] for p in active_products)
            
            all_products = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).label("total_qty"),
                func.max(DeliveryReport.dn_create_date).label("last_order")
            ).filter(
                DeliveryReport.product.isnot(None)
            ).group_by(
                DeliveryReport.product
            ).all()
            
            dead_stock = []
            for p in all_products:
                if p.product and p.product not in active_set:
                    dead_stock.append({
                        "product": p.product,
                        "total_qty": float(p.total_qty or 0),
                        "last_order": p.last_order,
                        "inactive_days": days
                    })
            
            dead_stock.sort(key=lambda x: x["total_qty"], reverse=True)
            return dead_stock[:limit]
            
        except Exception as e:
            logger.error(f"Dead stock products error: {e}")
            return []

    # ==================================================
    # PHASE 3: CITY ANALYTICS (Complete)
    # ==================================================

    def city_intelligence(self, city_name: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Complete city intelligence dashboard.
        Supports: "City sales", "Best city", "Worst city", "City analysis"
        """
        cache_key = f"city_intelligence:{city_name}"
        
        if not force_refresh and cache_key in self._city_cache:
            cached_time, cached_value = self._city_cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["city"]:
                return cached_value
        
        try:
            # Find city
            city = self._find_city(city_name)
            if not city:
                return {"success": False, "error": f"City '{city_name}' not found"}
            
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.ship_to_city == city
            ).all()
            
            if not records:
                return {"success": False, "error": f"No records found for city '{city}'"}
            
            # Calculate metrics
            total_dns = len(set(r.dn_no for r in records))
            total_value = sum(float(r.dn_amount or 0) for r in records)
            total_quantity = sum(float(r.dn_qty or 0) for r in records)
            
            pending_dns = len([r for r in records if r.pgi_status != "Completed"])
            pending_value = sum(float(r.dn_amount or 0) for r in records if r.pgi_status != "Completed")
            
            pod_pending_dns = len([r for r in records if r.pgi_status == "Completed" and r.pod_status != "Received"])
            
            # Delivery time analysis
            delivery_times = []
            for r in records:
                if r.good_issue_date and r.delivery_date:
                    delivery_time = (r.delivery_date - r.good_issue_date).days
                    if delivery_time >= 0:
                        delivery_times.append(delivery_time)
            avg_delivery_time = sum(delivery_times) / len(delivery_times) if delivery_times else 0
            
            # Risk score
            delay_rate = (pending_dns / total_dns * 100) if total_dns else 0
            risk_score = min(100, delay_rate * 1.5)
            
            if risk_score >= 70:
                risk_level = "Critical"
                risk_icon = "💀"
            elif risk_score >= 50:
                risk_level = "High"
                risk_icon = "🚨"
            elif risk_score >= 30:
                risk_level = "Medium"
                risk_icon = "⚠️"
            else:
                risk_level = "Low"
                risk_icon = "✅"
            
            # Top dealers in city
            dealer_sales = defaultdict(float)
            for r in records:
                if r.customer_name:
                    dealer_sales[r.customer_name] += float(r.dn_amount or 0)
            top_dealers = sorted(dealer_sales.items(), key=lambda x: x[1], reverse=True)[:5]
            
            result = {
                "success": True,
                "city": city,
                "total_dns": total_dns,
                "total_value": total_value,
                "total_quantity": total_quantity,
                "pending_dns": pending_dns,
                "pending_value": pending_value,
                "pod_pending_dns": pod_pending_dns,
                "avg_delivery_time": round(avg_delivery_time, 1),
                "delay_rate": round(delay_rate, 1),
                "risk_score": round(risk_score, 1),
                "risk_level": risk_level,
                "risk_icon": risk_icon,
                "top_dealers": [{"dealer": d, "value": v} for d, v in top_dealers],
                "formatted_message": self._format_city_intelligence(
                    city, total_value, total_dns, pending_dns, pod_pending_dns,
                    avg_delivery_time, risk_level, risk_icon, top_dealers
                )
            }
            
            self._city_cache[cache_key] = (datetime.utcnow(), result)
            return result
            
        except Exception as e:
            logger.error(f"City intelligence error: {e}")
            return {"success": False, "error": str(e)}

    def _format_city_intelligence(self, city: str, value: float, dns: int,
                                    pending_dns: int, pod_pending: int,
                                    avg_delivery: float, risk_level: str,
                                    risk_icon: str, top_dealers: List) -> str:
        """Format city intelligence for WhatsApp"""
        message = f"""
🌆 *CITY INTELLIGENCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 *CITY: {city}*

💰 *FINANCIALS*
• Total Value: Rs {value:,.2f}
• Total DNs: {dns}

⚠️ *PENDING*
• Pending DNs: {pending_dns}
• POD Pending: {pod_pending}
• Avg Delivery: {avg_delivery} days

{risk_icon} *RISK: {risk_level}*

🏪 *TOP DEALERS*
"""
        for dealer, val in top_dealers[:3]:
            message += f"• {dealer[:20]}: Rs {val:,.2f}\n"
        
        return message

    # ==================================================
    # PHASE 4: WAREHOUSE ANALYTICS (Complete)
    # ==================================================

    def warehouse_intelligence(self, warehouse_name: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Complete warehouse intelligence dashboard.
        Supports: "Warehouse performance", "Warehouse dispatch value", "Warehouse pending DN"
        """
        cache_key = f"warehouse_intelligence:{warehouse_name}"
        
        if not force_refresh and cache_key in self._warehouse_cache:
            cached_time, cached_value = self._warehouse_cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["warehouse"]:
                return cached_value
        
        try:
            warehouse = self._find_warehouse(warehouse_name)
            if not warehouse:
                return {"success": False, "error": f"Warehouse '{warehouse_name}' not found"}
            
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.warehouse == warehouse
            ).all()
            
            if not records:
                return {"success": False, "error": f"No records found for warehouse '{warehouse}'"}
            
            # Calculate metrics
            total_dns = len(set(r.dn_no for r in records))
            total_value = sum(float(r.dn_amount or 0) for r in records)
            total_quantity = sum(float(r.dn_qty or 0) for r in records)
            
            # Dispatch metrics (PGI completed)
            dispatched_dns = len([r for r in records if r.pgi_status == "Completed"])
            dispatched_value = sum(float(r.dn_amount or 0) for r in records if r.pgi_status == "Completed")
            dispatched_quantity = sum(float(r.dn_qty or 0) for r in records if r.pgi_status == "Completed")
            
            # Pending metrics
            pending_dns = total_dns - dispatched_dns
            pending_value = total_value - dispatched_value
            
            # POD metrics
            pod_pending_dns = len([r for r in records if r.pgi_status == "Completed" and r.pod_status != "Received"])
            
            # Average PGI time (DN create to PGI)
            pgi_times = []
            for r in records:
                if r.dn_create_date and r.good_issue_date:
                    pgi_time = (r.good_issue_date - r.dn_create_date).days
                    if pgi_time >= 0:
                        pgi_times.append(pgi_time)
            avg_pgi_time = sum(pgi_times) / len(pgi_times) if pgi_times else 0
            
            # Efficiency score
            efficiency = max(0, 100 - ((pending_dns / total_dns) * 100)) if total_dns else 0
            
            # Risk score
            risk_score = min(100, ((pending_dns / total_dns) * 50) + ((pod_pending_dns / total_dns) * 50)) if total_dns else 0
            
            if risk_score >= 70:
                risk_level = "Critical"
                risk_icon = "💀"
            elif risk_score >= 50:
                risk_level = "High"
                risk_icon = "🚨"
            elif risk_score >= 30:
                risk_level = "Medium"
                risk_icon = "⚠️"
            else:
                risk_level = "Low"
                risk_icon = "✅"
            
            result = {
                "success": True,
                "warehouse": warehouse,
                "total_dns": total_dns,
                "total_value": total_value,
                "total_quantity": total_quantity,
                "dispatched_dns": dispatched_dns,
                "dispatched_value": dispatched_value,
                "dispatched_quantity": dispatched_quantity,
                "pending_dns": pending_dns,
                "pending_value": pending_value,
                "pod_pending_dns": pod_pending_dns,
                "avg_pgi_time": round(avg_pgi_time, 1),
                "efficiency": round(efficiency, 1),
                "risk_score": round(risk_score, 1),
                "risk_level": risk_level,
                "risk_icon": risk_icon,
                "formatted_message": self._format_warehouse_intelligence(
                    warehouse, total_value, dispatched_value, pending_dns,
                    pod_pending_dns, avg_pgi_time, efficiency, risk_level, risk_icon
                )
            }
            
            self._warehouse_cache[cache_key] = (datetime.utcnow(), result)
            return result
            
        except Exception as e:
            logger.error(f"Warehouse intelligence error: {e}")
            return {"success": False, "error": str(e)}

    def _format_warehouse_intelligence(self, warehouse: str, total_value: float,
                                         dispatched_value: float, pending_dns: int,
                                         pod_pending: int, avg_pgi_time: float,
                                         efficiency: float, risk_level: str,
                                         risk_icon: str) -> str:
        """Format warehouse intelligence for WhatsApp"""
        return f"""
🏭 *WAREHOUSE INTELLIGENCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 *WAREHOUSE: {warehouse}*

💰 *VALUE*
• Total: Rs {total_value:,.2f}
• Dispatched: Rs {dispatched_value:,.2f}

📦 *METRICS*
• Pending DNs: {pending_dns}
• POD Pending: {pod_pending}
• Avg PGI Time: {avg_pgi_time} days

📊 *EFFICIENCY: {efficiency}%*
{risk_icon} *RISK: {risk_level}*

💡 Focus on clearing pending dispatches
"""

    # ==================================================
    # PHASE 5: EXECUTIVE DASHBOARD + MANAGEMENT INSIGHTS
    # ==================================================

    def executive_dashboard(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Complete executive dashboard.
        Supports: "Executive Summary", "CEO Dashboard", "Network Health"
        """
        cache_key = "executive_dashboard"
        
        if not force_refresh and cache_key in self._cache:
            cached_time, cached_value = self._cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["executive"]:
                return cached_value
        
        try:
            today = date.today()
            month_start = date(today.year, today.month, 1)
            year_start = date(today.year, 1, 1)
            
            # Sales metrics
            sales_today = self._get_sales_for_date(today)
            sales_mtd = self._get_sales_for_period(month_start, today)
            sales_ytd = self._get_sales_for_period(year_start, today)
            
            # DN metrics
            dns_created = self.db.query(func.count(DeliveryReport.dn_no)).scalar() or 0
            dns_delivered = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 0
            dns_pending = dns_created - dns_delivered
            
            # PGI and POD metrics
            pgi_pending = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            
            pod_pending = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).scalar() or 0
            
            # Revenue at risk
            pending_value = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            pod_pending_value = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).scalar() or 0
            revenue_at_risk = float(pending_value or 0) + float(pod_pending_value or 0)
            
            # Network health score
            health_score = self._calculate_network_health()
            
            if health_score >= 90:
                health_status = "Excellent"
                health_icon = "💎"
            elif health_score >= 80:
                health_status = "Good"
                health_icon = "✅"
            elif health_score >= 70:
                health_status = "Fair"
                health_icon = "⚠️"
            elif health_score >= 60:
                health_status = "Poor"
                health_icon = "🚨"
            else:
                health_status = "Critical"
                health_icon = "💀"
            
            # Top risks
            top_risk_dealers = self.high_risk_dealers(3)
            top_risk_cities = self._get_top_risk_cities(3)
            
            result = {
                "success": True,
                "sales_today": sales_today,
                "sales_mtd": sales_mtd,
                "sales_ytd": sales_ytd,
                "dns_created": dns_created,
                "dns_delivered": dns_delivered,
                "dns_pending": dns_pending,
                "pgi_pending": pgi_pending,
                "pod_pending": pod_pending,
                "revenue_at_risk": revenue_at_risk,
                "health_score": round(health_score, 1),
                "health_status": health_status,
                "health_icon": health_icon,
                "top_risk_dealers": top_risk_dealers,
                "top_risk_cities": top_risk_cities,
                "formatted_message": self._format_executive_dashboard(
                    sales_today, sales_mtd, sales_ytd, dns_created, dns_delivered,
                    dns_pending, pgi_pending, pod_pending, revenue_at_risk,
                    health_score, health_status, health_icon, top_risk_dealers, top_risk_cities
                )
            }
            
            self._cache[cache_key] = (datetime.utcnow(), result)
            return result
            
        except Exception as e:
            logger.error(f"Executive dashboard error: {e}")
            return {"success": False, "error": str(e)}

    def _format_executive_dashboard(self, sales_today: float, sales_mtd: float, 
                                      sales_ytd: float, dns_created: int,
                                      dns_delivered: int, dns_pending: int,
                                      pgi_pending: int, pod_pending: int,
                                      revenue_at_risk: float, health_score: float,
                                      health_status: str, health_icon: str,
                                      top_risk_dealers: List, top_risk_cities: List) -> str:
        """Format executive dashboard for WhatsApp"""
        message = f"""
👑 *EXECUTIVE DASHBOARD*
{health_icon} *NETWORK HEALTH: {health_score} ({health_status})*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *SALES*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Today: Rs {sales_today:,.2f}
• MTD: Rs {sales_mtd:,.2f}
• YTD: Rs {sales_ytd:,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 *OPERATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• DN Created: {dns_created}
• DN Delivered: {dns_delivered}
• DN Pending: {dns_pending}
• PGI Pending: {pgi_pending}
• POD Pending: {pod_pending}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *REVENUE AT RISK: Rs {revenue_at_risk:,.2f}*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🚨 *TOP RISK DEALERS*
"""
        for d in top_risk_dealers[:3]:
            message += f"• {d['dealer'][:20]}: {d['risk_score']}%\n"
        
        message += f"\n🌆 *TOP RISK CITIES*\n"
        for c in top_risk_cities[:3]:
            message += f"• {c['city'][:20]}: {c['risk_score']}%\n"
        
        return message

    def management_insights(self, focus: str = "general") -> Dict[str, Any]:
        """
        Generate management insights for business decisions.
        Supports: "Why sales decreased?", "Why POD delayed?", "Why Lahore declined?"
        """
        cache_key = f"management_insights:{focus}"
        
        if cache_key in self._cache:
            cached_time, cached_value = self._cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < 300:  # 5 minutes
                return cached_value
        
        try:
            insight = self._generate_management_insight(focus)
            
            result = {
                "success": True,
                "focus": focus,
                "insight": insight["insight"],
                "root_cause": insight["root_cause"],
                "impact": insight["impact"],
                "risk": insight["risk"],
                "recommended_action": insight["action"],
                "formatted_message": self._format_management_insight(
                    focus, insight["insight"], insight["root_cause"],
                    insight["impact"], insight["risk"], insight["action"]
                )
            }
            
            self._cache[cache_key] = (datetime.utcnow(), result)
            return result
            
        except Exception as e:
            logger.error(f"Management insights error: {e}")
            return {"success": False, "error": str(e)}

    def _generate_management_insight(self, focus: str) -> Dict:
        """Generate specific insight based on focus"""
        
        if "sales decreased" in focus.lower() or "sales down" in focus.lower():
            # Compare current month vs previous month
            today = date.today()
            current_month_start = date(today.year, today.month, 1)
            last_month_start = date(today.year, today.month - 1, 1) if today.month > 1 else date(today.year - 1, 12, 1)
            last_month_end = current_month_start - timedelta(days=1)
            
            current_sales = self._get_sales_for_period(current_month_start, today)
            last_sales = self._get_sales_for_period(last_month_start, last_month_end)
            
            decline_pct = ((last_sales - current_sales) / last_sales * 100) if last_sales else 0
            
            # Find top declining dealers
            declining_dealers = self._get_declining_dealers(5)
            
            return {
                "insight": f"Sales decreased by {decline_pct:.1f}% compared to last month",
                "root_cause": f"Primary decline from {declining_dealers[0]['dealer'] if declining_dealers else 'multiple dealers'}",
                "impact": f"Revenue loss of Rs {last_sales - current_sales:,.2f}",
                "risk": "Continued decline may impact monthly targets",
                "action": "Schedule recovery calls with top declining dealers"
            }
        
        elif "pod delayed" in focus.lower():
            pod_pending = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received",
                DeliveryReport.delivery_date <= date.today() - timedelta(days=3)
            ).scalar() or 0
            
            top_delayed_cities = self._get_top_pod_delayed_cities(3)
            
            return {
                "insight": f"{pod_pending} PODs pending beyond SLA",
                "root_cause": f"Primary delays in {', '.join([c['city'] for c in top_delayed_cities])}",
                "impact": f"Revenue at risk: Rs {self._get_pod_pending_value():,.2f}",
                "risk": "Prolonged delays affect cash flow and dealer confidence",
                "action": "Deploy automated reminders and manual follow-up for top 20 dealers"
            }
        
        elif "declined" in focus.lower():
            # Extract city from focus
            city = self._extract_city_from_text(focus)
            if city:
                city_data = self.city_intelligence(city)
                if city_data.get("success"):
                    return {
                        "insight": f"{city} shows {city_data.get('delay_rate', 0)}% delay rate",
                        "root_cause": f"{city_data.get('pending_dns', 0)} pending DNs",
                        "impact": f"Rs {city_data.get('pending_value', 0):,.2f} revenue at risk",
                        "risk": "High risk of dealer dissatisfaction",
                        "action": f"Deploy regional team to {city} for recovery"
                    }
        
        # Default insight
        return {
            "insight": "Network shows moderate delays in dispatch and POD collection",
            "root_cause": "Warehouse processing and dealer follow-up gaps",
            "impact": "Revenue realization delayed by 7-10 days on average",
            "risk": "Medium - Monitor high-risk dealers and cities",
            "action": "Focus on pending PGI and POD collection this week"
        }

    def _format_management_insight(self, focus: str, insight: str, 
                                     root_cause: str, impact: str, 
                                     risk: str, action: str) -> str:
        """Format management insight for WhatsApp"""
        return f"""
🧠 *MANAGEMENT INSIGHT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *FOCUS: {focus.upper()}*

💡 *INSIGHT*
{insight}

🔍 *ROOT CAUSE*
{root_cause}

💰 *IMPACT*
{impact}

⚠️ *RISK*
{risk}

✅ *RECOMMENDED ACTION*
{action}
"""

    # ==================================================
    # PHASE 6: EXCEPTION DASHBOARD
    # ==================================================

    def exception_dashboard(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Complete exception dashboard for operational monitoring.
        Supports: "Show delayed DN", "Show critical deliveries", "Show high value pending DN"
        """
        cache_key = "exception_dashboard"
        
        if not force_refresh and cache_key in self._cache:
            cached_time, cached_value = self._cache[cache_key]
            if (datetime.utcnow() - cached_time).seconds < self._cache_ttl["exception"]:
                return cached_value
        
        try:
            today = date.today()
            
            # DN aging buckets
            dn_3_plus = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_create_date <= today - timedelta(days=3)
            ).scalar() or 0
            
            dn_7_plus = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_create_date <= today - timedelta(days=7)
            ).scalar() or 0
            
            dn_15_plus = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_create_date <= today - timedelta(days=15)
            ).scalar() or 0
            
            dn_30_plus = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_create_date <= today - timedelta(days=30)
            ).scalar() or 0
            
            # High value pending DNs (> 1M)
            high_value_pending = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount
            ).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_amount > 1000000
            ).order_by(desc(DeliveryReport.dn_amount)).limit(10).all()
            
            # Critical delays (> 15 days) with details
            critical_delays = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_create_date <= today - timedelta(days=15)
            ).order_by(DeliveryReport.dn_create_date).limit(20).all()
            
            # Revenue at risk from exceptions
            revenue_at_risk = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            
            result = {
                "success": True,
                "dn_3_plus": dn_3_plus,
                "dn_7_plus": dn_7_plus,
                "dn_15_plus": dn_15_plus,
                "dn_30_plus": dn_30_plus,
                "high_value_pending": [
                    {"dn_no": h.dn_no, "dealer": h.customer_name, "value": float(h.dn_amount or 0)}
                    for h in high_value_pending
                ],
                "critical_delays": [
                    {"dn_no": c.dn_no, "dealer": c.customer_name, "value": float(c.dn_amount or 0), 
                     "days": (today - c.dn_create_date).days if c.dn_create_date else 0}
                    for c in critical_delays
                ],
                "revenue_at_risk": float(revenue_at_risk or 0),
                "formatted_message": self._format_exception_dashboard(
                    dn_3_plus, dn_7_plus, dn_15_plus, dn_30_plus,
                    high_value_pending, critical_delays, float(revenue_at_risk or 0)
                )
            }
            
            self._cache[cache_key] = (datetime.utcnow(), result)
            return result
            
        except Exception as e:
            logger.error(f"Exception dashboard error: {e}")
            return {"success": False, "error": str(e)}

    def _format_exception_dashboard(self, dn_3: int, dn_7: int, dn_15: int, dn_30: int,
                                      high_value: List, critical: List, revenue_risk: float) -> str:
        """Format exception dashboard for WhatsApp"""
        message = f"""
🚨 *EXCEPTION DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⏰ *DELAYED DNS*
• >3 days: {dn_3}
• >7 days: {dn_7}
• >15 days: {dn_15}
• >30 days: {dn_30}

💰 *REVENUE AT RISK: Rs {revenue_risk:,.2f}*

💎 *HIGH VALUE PENDING (>1M)*
"""
        for h in high_value[:5]:
            message += f"• {h['dn_no']}: {h['dealer'][:15]} (Rs {h['value']:,.2f})\n"
        
        message += f"\n⚠️ *CRITICAL DELAYS (>15 DAYS)*\n"
        for c in critical[:5]:
            message += f"• {c['dn_no']}: {c['dealer'][:15]} ({c['days']} days)\n"
        
        return message

    # ==================================================
    # HELPER METHODS
    # ==================================================

    def _get_sales_for_date(self, target_date: date) -> float:
        """Get sales for a specific date"""
        try:
            result = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                func.date(DeliveryReport.dn_create_date) == target_date
            ).scalar()
            return float(result or 0)
        except:
            return 0

    def _get_sales_for_period(self, start_date: date, end_date: date) -> float:
        """Get sales for a date period"""
        try:
            result = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.dn_create_date >= start_date,
                DeliveryReport.dn_create_date <= end_date
            ).scalar()
            return float(result or 0)
        except:
            return 0

    def _calculate_network_health(self) -> float:
        """Calculate overall network health score"""
        try:
            total_dns = self.db.query(func.count(DeliveryReport.dn_no)).scalar() or 1
            delivered = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 0
            pod_done = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pod_status == "Received"
            ).scalar() or 0
            
            delivery_score = (delivered / total_dns) * 100
            pod_score = (pod_done / total_dns) * 100
            
            return (delivery_score * 0.6) + (pod_score * 0.4)
        except:
            return 50

    def _get_top_risk_cities(self, limit: int = 5) -> List[Dict]:
        """Get top risk cities"""
        try:
            results = self.db.query(
                DeliveryReport.ship_to_city,
                func.count(DeliveryReport.dn_no).label("total"),
                func.count(case((DeliveryReport.pgi_status != "Completed", 1), else_=None)).label("pending")
            ).filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).group_by(
                DeliveryReport.ship_to_city
            ).all()
            
            cities = []
            for r in results:
                if r.total and r.ship_to_city:
                    risk = (r.pending / r.total) * 100 if r.total else 0
                    cities.append({
                        "city": r.ship_to_city,
                        "risk_score": round(risk, 1)
                    })
            
            cities.sort(key=lambda x: x["risk_score"], reverse=True)
            return cities[:limit]
        except:
            return []

    def _get_declining_dealers(self, limit: int = 5) -> List[Dict]:
        """Get dealers with declining sales"""
        # Simplified - compare last 30 vs previous 30 days
        try:
            today = date.today()
            last_30_start = today - timedelta(days=30)
            prev_30_start = today - timedelta(days=60)
            prev_30_end = today - timedelta(days=31)
            
            results = self.db.query(
                DeliveryReport.customer_name,
                func.sum(case((DeliveryReport.dn_create_date >= last_30_start, 
                              DeliveryReport.dn_amount), else_=0)).label("recent"),
                func.sum(case((and_(DeliveryReport.dn_create_date >= prev_30_start,
                                    DeliveryReport.dn_create_date <= prev_30_end),
                              DeliveryReport.dn_amount), else_=0)).label("previous")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).having(
                func.sum(case((DeliveryReport.dn_create_date >= prev_30_start,
                              DeliveryReport.dn_amount), else_=0)) > 0
            ).all()
            
            dealers = []
            for r in results:
                if r.customer_name and r.previous > 0:
                    decline = ((r.previous - r.recent) / r.previous) * 100
                    if decline > 20:  # More than 20% decline
                        dealers.append({
                            "dealer": r.customer_name,
                            "decline_pct": round(decline, 1)
                        })
            
            dealers.sort(key=lambda x: x["decline_pct"], reverse=True)
            return dealers[:limit]
        except:
            return []

    def _get_top_pod_delayed_cities(self, limit: int = 5) -> List[Dict]:
        """Get cities with most POD delays"""
        try:
            results = self.db.query(
                DeliveryReport.ship_to_city,
                func.count(DeliveryReport.dn_no).label("total"),
                func.count(case((and_(DeliveryReport.pgi_status == "Completed",
                                      DeliveryReport.pod_status != "Received"), 1), else_=None)).label("pod_pending")
            ).filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).group_by(
                DeliveryReport.ship_to_city
            ).having(
                func.count(case((and_(DeliveryReport.pgi_status == "Completed",
                                      DeliveryReport.pod_status != "Received"), 1), else_=None)) > 0
            ).all()
            
            cities = []
            for r in results:
                if r.ship_to_city:
                    cities.append({
                        "city": r.ship_to_city,
                        "pod_pending": r.pod_pending
                    })
            
            cities.sort(key=lambda x: x["pod_pending"], reverse=True)
            return cities[:limit]
        except:
            return []

    def _get_pod_pending_value(self) -> float:
        """Get total value of pending PODs"""
        try:
            result = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).scalar()
            return float(result or 0)
        except:
            return 0

    def _extract_city_from_text(self, text: str) -> Optional[str]:
        """Extract city name from text"""
        cities = ['karachi', 'lahore', 'islamabad', 'rawalpindi', 'faisalabad',
                  'multan', 'peshawar', 'quetta', 'gujranwala', 'sialkot']
        text_lower = text.lower()
        for city in cities:
            if city in text_lower:
                return city.title()
        return None

    def _get_sales_manager_for_city(self, city: str) -> str:
        """Get sales manager for a city"""
        managers = {
            "Karachi": "Ali Raza",
            "Lahore": "Ahmed Khan",
            "Islamabad": "Sara Khan",
            "Rawalpindi": "Usman Ali",
            "Faisalabad": "Bilal Ahmed"
        }
        return managers.get(city, "Regional Manager")

    def _find_product(self, product_name: str) -> Optional[str]:
        """Find product using fuzzy matching"""
        products = self.db.query(DeliveryReport.product).distinct().filter(
            DeliveryReport.product.isnot(None)
        ).limit(500).all()
        
        product_list = [p[0] for p in products if p[0]]
        
        if not product_list:
            return None
        
        product_upper = product_name.upper()
        for p in product_list:
            if p.upper() == product_upper:
                return p
        
        for p in product_list:
            if product_upper in p.upper() or p.upper() in product_upper:
                return p
        
        return None

    def _find_city(self, city_name: str) -> Optional[str]:
        """Find city using fuzzy matching"""
        cities = self.db.query(DeliveryReport.ship_to_city).distinct().filter(
            DeliveryReport.ship_to_city.isnot(None)
        ).limit(100).all()
        
        city_list = [c[0] for c in cities if c[0]]
        
        if not city_list:
            return None
        
        city_lower = city_name.lower()
        for c in city_list:
            if c.lower() == city_lower:
                return c
        
        for c in city_list:
            if city_lower in c.lower() or c.lower() in city_lower:
                return c
        
        return None

    def _find_warehouse(self, warehouse_name: str) -> Optional[str]:
        """Find warehouse using fuzzy matching"""
        warehouses = self.db.query(DeliveryReport.warehouse).distinct().filter(
            DeliveryReport.warehouse.isnot(None)
        ).limit(100).all()
        
        warehouse_list = [w[0] for w in warehouses if w[0]]
        
        if not warehouse_list:
            return None
        
        warehouse_upper = warehouse_name.upper()
        for w in warehouse_list:
            if w.upper() == warehouse_upper:
                return w
        
        for w in warehouse_list:
            if warehouse_upper in w.upper() or w.upper() in warehouse_upper:
                return w
        
        return None


# ==================================================
# FACTORY FUNCTION
# ==================================================

def get_analytics_service(db: Session) -> AnalyticsService:
    """Get analytics service instance"""
    return AnalyticsService(db)


# ==================================================
# COMPATIBILITY METHODS (Keep existing interface)
# ==================================================

def dealer_health_score(db: Session, dealer_name: str) -> Dict:
    """Compatibility wrapper for dealer_health_score"""
    service = AnalyticsService(db)
    return service.get_complete_dealer_dashboard(dealer_name)

def product_analytics(db: Session, product_code: str) -> Dict:
    """Compatibility wrapper for product analytics"""
    service = AnalyticsService(db)
    return service.product_intelligence(product_code)

def executive_summary(db: Session) -> Dict:
    """Compatibility wrapper for executive summary"""
    service = AnalyticsService(db)
    return service.executive_dashboard()
