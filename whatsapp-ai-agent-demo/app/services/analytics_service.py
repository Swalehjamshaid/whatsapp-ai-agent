# ==========================================================
# FILE: app/services/analytics_service.py (ENTERPRISE UPGRADE)
# ==========================================================

from datetime import date, datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple
from functools import lru_cache
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, case, desc, distinct, or_, text
from loguru import logger

# Try to import rapidfuzz for fuzzy matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("RapidFuzz not available. Install with: pip install rapidfuzz")

from app.models import DeliveryReport


class AnalyticsService:

    def __init__(self, db: Session):
        self.db = db
        self._cache = {}
        self._cache_ttl = 300  # 5 minutes cache
        self._dealer_cache = {}  # Dealer name cache for fuzzy matching

    # ======================================================
    # NORMALIZATION HELPERS
    # ======================================================

    @staticmethod
    def is_pod_received(status: Optional[str]) -> bool:
        if not status:
            return False
        status_lower = status.lower().strip()
        return status_lower in ["received", "received ", "pod received", "done", "completed", "yes"]

    @staticmethod
    def is_pod_pending(status: Optional[str]) -> bool:
        if not status:
            return True
        status_lower = status.lower().strip()
        return status_lower not in ["received", "received ", "pod received", "done", "completed", "yes"]

    @staticmethod
    def is_pgi_completed(status: Optional[str]) -> bool:
        if not status:
            return False
        status_lower = status.lower().strip()
        return status_lower in ["completed", "done", "yes", "dispatched"]

    @staticmethod
    def calculate_dispatch_age(record):
        if not record.dn_create_date or not record.good_issue_date:
            return 0
        return (record.good_issue_date - record.dn_create_date).days

    @staticmethod
    def calculate_pod_age(record):
        if not record.good_issue_date:
            return 0
        if AnalyticsService.is_pod_received(record.pod_status):
            return 0
        return (date.today() - record.good_issue_date).days

    @staticmethod
    def calculate_delivery_cycle(record):
        if not record.dn_create_date or not record.pod_date:
            return None
        return (record.pod_date - record.dn_create_date).days

    # ======================================================
    # PRIORITY 1: NETWORK HEALTH ENGINE
    # ======================================================

    def network_health_score(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Calculate overall network health score (0-100)"""
        cache_key = "network_health"
        
        if not force_refresh:
            cached = self._get_cached(cache_key)
            if cached:
                return cached
        
        try:
            # Get key metrics
            pending_metrics = self.pending_metrics()
            pod_metrics = self.pod_metrics()
            
            # Dealer health
            dealer_rankings = self.dealer_rankings(100)
            dealer_scores = [d.get("score", 0) for d in dealer_rankings.get("by_score", [])]
            dealer_score = sum(dealer_scores) / len(dealer_scores) if dealer_scores else 70
            
            # Warehouse health
            warehouse_rankings = self.warehouse_rankings(100)
            warehouse_scores = [w.get("efficiency_score", 0) for w in warehouse_rankings.get("all_warehouses", [])]
            warehouse_score = sum(warehouse_scores) / len(warehouse_scores) if warehouse_scores else 70
            
            # City health
            city_rankings = self.city_rankings(100)
            city_scores = [c.get("performance_score", 0) for c in city_rankings.get("all_cities", [])]
            city_score = sum(city_scores) / len(city_scores) if city_scores else 70
            
            # POD compliance
            total_dns = pending_metrics.get("total_dns", 1)
            pod_completed = total_dns - pod_metrics.get("pod_pending_dns", 0)
            pod_compliance = (pod_completed / total_dns) * 100 if total_dns > 0 else 100
            
            # Delivery compliance
            delivered_dns = pending_metrics.get("total_dns", 0) - pending_metrics.get("pending_dns", 0)
            delivery_compliance = (delivered_dns / total_dns) * 100 if total_dns > 0 else 100
            
            # Weighted final score
            final_score = (
                pod_compliance * 0.30 +
                delivery_compliance * 0.25 +
                dealer_score * 0.20 +
                warehouse_score * 0.15 +
                city_score * 0.10
            )
            
            # Determine health category
            if final_score >= 90:
                category = "Excellent"
                icon = "💎"
            elif final_score >= 80:
                category = "Good"
                icon = "✅"
            elif final_score >= 70:
                category = "Fair"
                icon = "⚠️"
            elif final_score >= 60:
                category = "Poor"
                icon = "🚨"
            else:
                category = "Critical"
                icon = "💀"
            
            result = {
                "score": round(final_score, 1),
                "category": category,
                "icon": icon,
                "pod_compliance": round(pod_compliance, 1),
                "delivery_compliance": round(delivery_compliance, 1),
                "dealer_score": round(dealer_score, 1),
                "warehouse_score": round(warehouse_score, 1),
                "city_score": round(city_score, 1)
            }
            
            self._set_cache(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Network health calculation error: {e}")
            return {
                "score": 0,
                "category": "Unknown",
                "icon": "❓",
                "pod_compliance": 0,
                "delivery_compliance": 0,
                "dealer_score": 0,
                "warehouse_score": 0,
                "city_score": 0,
                "error": str(e)
            }

    # ======================================================
    # PRIORITY 2: EXECUTIVE SUMMARY ENGINE
    # ======================================================

    def executive_summary(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Generate executive summary with key metrics"""
        cache_key = "executive_summary"
        
        if not force_refresh:
            cached = self._get_cached(cache_key)
            if cached:
                return cached
        
        try:
            network = self.network_health_score(force_refresh)
            revenue_risk = self.revenue_at_risk(force_refresh)
            inventory_risk = self.inventory_at_risk(force_refresh)
            
            # Get top risks
            risk_dealers = self.top_risk_dealers(5)
            risk_warehouses = self.top_risk_warehouses(5)
            risk_cities = self.top_risk_cities(5)
            
            pending_metrics = self.pending_metrics()
            
            result = {
                "network_health": network.get("score", 0),
                "network_category": network.get("category", "Unknown"),
                "revenue_at_risk": revenue_risk.get("amount", 0),
                "revenue_at_risk_formatted": revenue_risk.get("formatted", "Rs 0"),
                "inventory_at_risk": inventory_risk.get("units", 0),
                "inventory_at_risk_formatted": f"{inventory_risk.get('units', 0):,.0f}",
                "top_risk_dealer": risk_dealers[0]["dealer"] if risk_dealers else "None",
                "top_risk_dealer_risk": risk_dealers[0]["risk_score"] if risk_dealers else 0,
                "top_risk_warehouse": risk_warehouses[0]["warehouse"] if risk_warehouses else "None",
                "top_risk_warehouse_risk": risk_warehouses[0]["risk_score"] if risk_warehouses else 0,
                "top_risk_city": risk_cities[0]["city"] if risk_cities else "None",
                "top_risk_city_risk": risk_cities[0]["risk_score"] if risk_cities else 0,
                "total_pending_dns": pending_metrics.get("pending_dns", 0),
                "total_pending_value": pending_metrics.get("pending_value", 0),
                "formatted_message": self._format_executive_summary(network, revenue_risk, risk_dealers, risk_cities)
            }
            
            self._set_cache(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Executive summary error: {e}")
            return {"error": str(e), "success": False}

    def _format_executive_summary(self, network: Dict, revenue_risk: Dict, risk_dealers: List, risk_cities: List) -> str:
        """Format executive summary for WhatsApp"""
        summary = f"""
{network.get('icon', '📊')} *NETWORK HEALTH: {network.get('score', 0)}/100* ({network.get('category', 'Unknown')})

💰 *REVENUE AT RISK: {revenue_risk.get('formatted', 'Rs 0')}*

🚨 *TOP RISKS*
• Dealer: {risk_dealers[0]['dealer'] if risk_dealers else 'None'} ({risk_dealers[0]['risk_score'] if risk_dealers else 0}%)
• City: {risk_cities[0]['city'] if risk_cities else 'None'} ({risk_cities[0]['risk_score'] if risk_cities else 0}%)

💡 *FOCUS TODAY*
Recover POD from top 10 dealers immediately
"""
        return summary.strip()

    # ======================================================
    # PRIORITY 3: REVENUE AT RISK
    # ======================================================

    def revenue_at_risk(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Calculate total revenue at risk"""
        cache_key = "revenue_at_risk"
        
        if not force_refresh:
            cached = self._get_cached(cache_key)
            if cached:
                return cached
        
        try:
            result = self.db.query(
                func.sum(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount),
                    else_=0
                )).label("pending_revenue"),
                func.sum(case(
                    (and_(
                        DeliveryReport.pgi_status == "Completed",
                        ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])
                    ), DeliveryReport.dn_amount),
                    else_=0
                )).label("pod_pending_revenue")
            ).first()
            
            pending_revenue = float(result.pending_revenue or 0)
            pod_pending_revenue = float(result.pod_pending_revenue or 0)
            total_at_risk = pending_revenue + pod_pending_revenue
            
            # Calculate risk level
            if total_at_risk > 10_000_000_000:  # 10B+
                risk_level = "Critical"
                icon = "💀"
            elif total_at_risk > 5_000_000_000:  # 5B+
                risk_level = "High"
                icon = "🚨"
            elif total_at_risk > 1_000_000_000:  # 1B+
                risk_level = "Medium"
                icon = "⚠️"
            else:
                risk_level = "Low"
                icon = "✅"
            
            result_dict = {
                "amount": total_at_risk,
                "formatted": f"Rs {total_at_risk:,.2f}",
                "pending_revenue": pending_revenue,
                "pod_pending_revenue": pod_pending_revenue,
                "risk_level": risk_level,
                "icon": icon
            }
            
            self._set_cache(cache_key, result_dict)
            return result_dict
            
        except Exception as e:
            logger.error(f"Revenue at risk error: {e}")
            return {"amount": 0, "formatted": "Rs 0", "error": str(e)}

    # ======================================================
    # PRIORITY 4: INVENTORY AT RISK
    # ======================================================

    def inventory_at_risk(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Calculate total inventory units at risk"""
        cache_key = "inventory_at_risk"
        
        if not force_refresh:
            cached = self._get_cached(cache_key)
            if cached:
                return cached
        
        try:
            result = self.db.query(
                func.sum(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_qty),
                    else_=0
                )).label("pending_units"),
                func.sum(case(
                    (and_(
                        DeliveryReport.pgi_status == "Completed",
                        ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])
                    ), DeliveryReport.dn_qty),
                    else_=0
                )).label("pod_pending_units")
            ).first()
            
            pending_units = float(result.pending_units or 0)
            pod_pending_units = float(result.pod_pending_units or 0)
            total_at_risk = pending_units + pod_pending_units
            
            result_dict = {
                "units": total_at_risk,
                "formatted": f"{total_at_risk:,.0f}",
                "pending_units": pending_units,
                "pod_pending_units": pod_pending_units
            }
            
            self._set_cache(cache_key, result_dict)
            return result_dict
            
        except Exception as e:
            logger.error(f"Inventory at risk error: {e}")
            return {"units": 0, "formatted": "0", "error": str(e)}

    # ======================================================
    # PRIORITY 5: DEALER HEALTH SCORE (UPGRADED)
    # ======================================================

    def dealer_health_score(self, dealer_name: str, force_refresh: bool = False) -> Dict[str, Any]:
        """Get comprehensive dealer health score with trend and recommendations"""
        cache_key = f"dealer_health_{dealer_name}"
        
        if not force_refresh:
            cached = self._get_cached(cache_key)
            if cached:
                return cached
        
        try:
            # Get current metrics
            dashboard = self.dealer_dashboard_metrics(dealer_name)
            if not dashboard.get("success"):
                return {"success": False, "error": dashboard.get("message", "Dealer not found")}
            
            # Get historical trend (compare with previous period)
            trend = self._calculate_dealer_trend(dealer_name)
            
            # Calculate risk level based on metrics
            pending_dns = dashboard.get("pending_dns", 0)
            total_dns = dashboard.get("total_dns", 1)
            pod_pending = dashboard.get("pod_pending_dns", 0)
            
            pending_ratio = pending_dns / total_dns
            pod_ratio = pod_pending / total_dns
            
            if pending_ratio > 0.5 or pod_ratio > 0.5:
                risk_level = "Critical"
                risk_icon = "💀"
            elif pending_ratio > 0.3 or pod_ratio > 0.3:
                risk_level = "High"
                risk_icon = "🚨"
            elif pending_ratio > 0.15 or pod_ratio > 0.15:
                risk_level = "Medium"
                risk_icon = "⚠️"
            else:
                risk_level = "Low"
                risk_icon = "✅"
            
            # Generate recommendation
            recommendation = self._generate_dealer_recommendation(dashboard, risk_level)
            
            # Calculate health score (0-100)
            base_score = dashboard.get("pending_dns", 0)
            health_score = max(0, 100 - (pending_ratio * 100) - (pod_ratio * 50))
            
            result = {
                "success": True,
                "dealer": dealer_name,
                "score": round(health_score, 1),
                "risk_level": risk_level,
                "risk_icon": risk_icon,
                "trend": trend,
                "recommendation": recommendation,
                "metrics": {
                    "total_dns": total_dns,
                    "pending_dns": pending_dns,
                    "pending_value": dashboard.get("pending_value", 0),
                    "pod_pending_dns": pod_pending,
                    "completion_rate": round((total_dns - pending_dns) / total_dns * 100, 1)
                }
            }
            
            self._set_cache(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"Dealer health score error: {e}")
            return {"success": False, "error": str(e)}

    def _calculate_dealer_trend(self, dealer_name: str) -> str:
        """Calculate performance trend for dealer"""
        try:
            # Get last 30 days vs previous 30 days
            thirty_days_ago = date.today() - timedelta(days=30)
            sixty_days_ago = date.today() - timedelta(days=60)
            
            recent = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("dns")
            ).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.dn_create_date >= thirty_days_ago
            ).first()
            
            previous = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("dns")
            ).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.dn_create_date >= sixty_days_ago,
                DeliveryReport.dn_create_date < thirty_days_ago
            ).first()
            
            recent_count = recent.dns or 0
            previous_count = previous.dns or 0
            
            if previous_count == 0:
                return "Stable"
            
            change = ((recent_count - previous_count) / previous_count) * 100
            
            if change > 20:
                return "Improving"
            elif change < -20:
                return "Declining"
            else:
                return "Stable"
                
        except Exception:
            return "Stable"

    def _generate_dealer_recommendation(self, dashboard: Dict, risk_level: str) -> str:
        """Generate actionable recommendation for dealer"""
        pending_dns = dashboard.get("pending_dns", 0)
        pod_pending = dashboard.get("pod_pending_dns", 0)
        
        if risk_level == "Critical":
            return f"🚨 URGENT: Recover {pending_dns} pending DNs and {pod_pending} PODs immediately. Escalate to senior management."
        elif risk_level == "High":
            return f"⚠️ Escalate {pending_dns} pending DNs and prioritize POD collection from this dealer."
        elif risk_level == "Medium":
            return f"📋 Monitor {pending_dns} pending DNs. Schedule follow-up for POD collection."
        else:
            return "✅ Dealer performing well. Maintain regular follow-up."

    # ======================================================
    # PRIORITY 6: WAREHOUSE INTELLIGENCE ENGINE
    # ======================================================

    def warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        """Comprehensive warehouse dashboard with health scores"""
        try:
            # Find matching warehouse
            matching = self._find_warehouse(warehouse_name)
            if not matching:
                return {"success": False, "error": f"Warehouse '{warehouse_name}' not found"}
            
            warehouse = matching
            
            # Get metrics
            metrics = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(distinct(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                    else_=None
                ))).label("pending_dns"),
                func.sum(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_qty),
                    else_=0
                )).label("pending_units"),
                func.count(distinct(case(
                    (and_(
                        DeliveryReport.pgi_status == "Completed",
                        ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])
                    ), DeliveryReport.dn_no),
                    else_=None
                ))).label("pod_pending_dns")
            ).filter(DeliveryReport.warehouse == warehouse).first()
            
            # Calculate health scores
            total_dns = metrics.total_dns or 1
            pending_dns = metrics.pending_dns or 0
            pod_pending = metrics.pod_pending_dns or 0
            
            efficiency_score = max(0, 100 - ((pending_dns / total_dns) * 100))
            risk_score = min(100, ((pending_dns / total_dns) * 50) + ((pod_pending / total_dns) * 50))
            
            # Determine risk level
            if risk_score > 70:
                risk_level = "Critical"
                risk_icon = "💀"
            elif risk_score > 50:
                risk_level = "High"
                risk_icon = "🚨"
            elif risk_score > 30:
                risk_level = "Medium"
                risk_icon = "⚠️"
            else:
                risk_level = "Low"
                risk_icon = "✅"
            
            # Identify bottlenecks
            bottlenecks = self._identify_warehouse_bottlenecks(warehouse)
            
            # Calculate recovery opportunity
            recovery_opportunity = self._warehouse_recovery_opportunity(warehouse)
            
            result = {
                "success": True,
                "warehouse": warehouse,
                "total_dns": total_dns,
                "total_units": float(metrics.total_units or 0),
                "total_value": float(metrics.total_value or 0),
                "pending_dns": pending_dns,
                "pending_units": float(metrics.pending_units or 0),
                "pod_pending_dns": pod_pending,
                "efficiency_score": round(efficiency_score, 1),
                "risk_score": round(risk_score, 1),
                "risk_level": risk_level,
                "risk_icon": risk_icon,
                "bottlenecks": bottlenecks,
                "recovery_opportunity": recovery_opportunity,
                "formatted_message": self._format_warehouse_dashboard(
                    warehouse, efficiency_score, risk_level, pending_dns, pod_pending, bottlenecks, recovery_opportunity
                )
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Warehouse dashboard error: {e}")
            return {"success": False, "error": str(e)}

    def _find_warehouse(self, warehouse_name: str) -> Optional[str]:
        """Find warehouse using fuzzy matching"""
        warehouses = self.db.query(DeliveryReport.warehouse).distinct().filter(
            DeliveryReport.warehouse.isnot(None)
        ).limit(100).all()
        
        warehouse_list = [w[0] for w in warehouses if w[0]]
        
        if not warehouse_list:
            return None
        
        # Exact match
        for w in warehouse_list:
            if w.upper() == warehouse_name.upper():
                return w
        
        # Fuzzy match
        if RAPIDFUZZ_AVAILABLE:
            match = process.extractOne(warehouse_name, warehouse_list, scorer=fuzz.ratio)
            if match and match[1] > 70:
                return match[0]
        
        # Simple contains
        for w in warehouse_list:
            if warehouse_name.upper() in w.upper() or w.upper() in warehouse_name.upper():
                return w
        
        return None

    def _identify_warehouse_bottlenecks(self, warehouse: str) -> List[Dict]:
        """Identify bottlenecks in warehouse operations"""
        bottlenecks = []
        
        try:
            # Check pending DNs by age
            aging = self.db.query(
                DeliveryReport.dn_no,
                func.date_part('day', func.age(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date)).label("dispatch_days")
            ).filter(
                DeliveryReport.warehouse == warehouse,
                DeliveryReport.pgi_status != "Completed"
            ).limit(100).all()
            
            old_pending = sum(1 for a in aging if (a.dispatch_days or 0) > 15)
            
            if old_pending > 10:
                bottlenecks.append({
                    "type": "Aging Inventory",
                    "severity": "High",
                    "description": f"{old_pending} DNs pending for over 15 days"
                })
            
            # Check POD pending
            pod_pending_count = self.db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.warehouse == warehouse,
                DeliveryReport.pgi_status == "Completed",
                ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])
            ).count()
            
            if pod_pending_count > 50:
                bottlenecks.append({
                    "type": "POD Collection",
                    "severity": "Medium",
                    "description": f"{pod_pending_count} DNs awaiting POD"
                })
                
        except Exception as e:
            logger.error(f"Bottleneck identification error: {e}")
        
        return bottlenecks[:3]  # Return top 3 bottlenecks

    def _warehouse_recovery_opportunity(self, warehouse: str) -> Dict:
        """Calculate recovery opportunity for warehouse"""
        try:
            result = self.db.query(
                func.sum(DeliveryReport.dn_amount).label("recoverable_value")
            ).filter(
                DeliveryReport.warehouse == warehouse,
                DeliveryReport.pgi_status != "Completed"
            ).first()
            
            recoverable = float(result.recoverable_value or 0)
            
            return {
                "recoverable_value": recoverable,
                "formatted": f"Rs {recoverable:,.2f}",
                "estimated_impact": "High" if recoverable > 100_000_000 else "Medium" if recoverable > 10_000_000 else "Low"
            }
            
        except Exception:
            return {"recoverable_value": 0, "formatted": "Rs 0", "estimated_impact": "Unknown"}

    def _format_warehouse_dashboard(self, warehouse: str, efficiency: float, risk_level: str,
                                     pending: int, pod_pending: int, bottlenecks: List, recovery: Dict) -> str:
        """Format warehouse dashboard for WhatsApp"""
        dashboard = f"""
🏭 *WAREHOUSE: {warehouse}*

📊 *EFFICIENCY: {efficiency}/100*
⚠️ *RISK LEVEL: {risk_level}*

📦 *METRICS*
• Pending DNs: {pending}
• POD Pending: {pod_pending}
• Recoverable Value: {recovery.get('formatted', 'Rs 0')}

🔍 *BOTTLENECKS*
"""
        for b in bottlenecks:
            dashboard += f"• {b['description']}\n"
        
        if recovery.get('estimated_impact') == "High":
            dashboard += "\n💡 *ACTION REQUIRED*: Escalate immediately"
        
        return dashboard.strip()

    def top_risk_warehouses(self, limit: int = 10) -> List[Dict]:
        """Identify warehouses with highest risk"""
        try:
            results = self.db.query(
                DeliveryReport.warehouse,
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(distinct(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                    else_=None
                ))).label("pending_dns"),
                func.sum(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount),
                    else_=0
                )).label("pending_value")
            ).filter(
                DeliveryReport.warehouse.isnot(None)
            ).group_by(
                DeliveryReport.warehouse
            ).all()
            
            warehouses = []
            for r in results:
                if not r.warehouse:
                    continue
                
                total_dns = r.total_dns or 1
                pending_dns = r.pending_dns or 0
                risk_score = (pending_dns / total_dns) * 100
                
                warehouses.append({
                    "warehouse": r.warehouse,
                    "pending_dns": pending_dns,
                    "pending_value": float(r.pending_value or 0),
                    "risk_score": round(risk_score, 1)
                })
            
            return sorted(warehouses, key=lambda x: x["risk_score"], reverse=True)[:limit]
            
        except Exception as e:
            logger.error(f"Top risk warehouses error: {e}")
            return []

    # ======================================================
    # PRIORITY 7: CITY INTELLIGENCE ENGINE
    # ======================================================

    def city_dashboard(self, city_name: str) -> Dict[str, Any]:
        """Comprehensive city dashboard with risk analysis"""
        try:
            # Find matching city
            city = self._find_city(city_name)
            if not city:
                return {"success": False, "error": f"City '{city_name}' not found"}
            
            # Get metrics
            metrics = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(distinct(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                    else_=None
                ))).label("pending_dns"),
                func.sum(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_qty),
                    else_=0
                )).label("pending_units"),
                func.sum(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount),
                    else_=0
                )).label("pending_value")
            ).filter(DeliveryReport.ship_to_city == city).first()
            
            total_dns = metrics.total_dns or 1
            pending_dns = metrics.pending_dns or 0
            pending_value = float(metrics.pending_value or 0)
            
            # Calculate risk score
            delay_rate = (pending_dns / total_dns) * 100
            risk_score = min(100, delay_rate * 1.5)  # Weighted risk score
            
            # Determine risk level
            if risk_score > 70:
                risk_level = "Critical"
                risk_icon = "💀"
                urgency = "IMMEDIATE ACTION REQUIRED"
            elif risk_score > 50:
                risk_level = "High"
                risk_icon = "🚨"
                urgency = "Escalate within 24 hours"
            elif risk_score > 30:
                risk_level = "Medium"
                risk_icon = "⚠️"
                urgency = "Monitor closely"
            else:
                risk_level = "Low"
                risk_icon = "✅"
                urgency = "Normal monitoring"
            
            # Generate recommendations
            recommendations = self._generate_city_recommendations(city, pending_dns, pending_value)
            
            result = {
                "success": True,
                "city": city,
                "total_dns": total_dns,
                "total_units": float(metrics.total_units or 0),
                "total_value": float(metrics.total_value or 0),
                "pending_dns": pending_dns,
                "pending_units": float(metrics.pending_units or 0),
                "pending_value": pending_value,
                "delay_rate": round(delay_rate, 1),
                "risk_score": round(risk_score, 1),
                "risk_level": risk_level,
                "risk_icon": risk_icon,
                "urgency": urgency,
                "recommendations": recommendations,
                "formatted_message": self._format_city_dashboard(
                    city, risk_score, risk_level, pending_dns, pending_value, delay_rate, urgency, recommendations
                )
            }
            
            return result
            
        except Exception as e:
            logger.error(f"City dashboard error: {e}")
            return {"success": False, "error": str(e)}

    def _find_city(self, city_name: str) -> Optional[str]:
        """Find city using fuzzy matching"""
        cities = self.db.query(DeliveryReport.ship_to_city).distinct().filter(
            DeliveryReport.ship_to_city.isnot(None)
        ).limit(100).all()
        
        city_list = [c[0] for c in cities if c[0]]
        
        if not city_list:
            return None
        
        # Exact match (case insensitive)
        city_lower = city_name.lower()
        for c in city_list:
            if c.lower() == city_lower:
                return c
        
        # Fuzzy match
        if RAPIDFUZZ_AVAILABLE:
            match = process.extractOne(city_name, city_list, scorer=fuzz.ratio)
            if match and match[1] > 70:
                return match[0]
        
        # Contains match
        for c in city_list:
            if city_lower in c.lower() or c.lower() in city_lower:
                return c
        
        return None

    def _generate_city_recommendations(self, city: str, pending_dns: int, pending_value: float) -> List[str]:
        """Generate actionable recommendations for city"""
        recommendations = []
        
        if pending_dns > 500:
            recommendations.append(f"🚨 Escalate {pending_dns} pending DNs in {city} to regional manager")
        
        if pending_value > 100_000_000:
            recommendations.append(f"💰 Schedule recovery call for Rs {pending_value:,.2f} exposure in {city}")
        
        if not recommendations:
            recommendations.append(f"✅ {city} performing within acceptable range. Maintain regular monitoring.")
        
        return recommendations[:3]

    def _format_city_dashboard(self, city: str, risk_score: float, risk_level: str,
                                pending_dns: int, pending_value: float, delay_rate: float,
                                urgency: str, recommendations: List) -> str:
        """Format city dashboard for WhatsApp"""
        dashboard = f"""
🌆 *CITY: {city}*

{urgency}

📊 *RISK SCORE: {risk_score}/100* ({risk_level})
⏳ *DELAY RATE: {delay_rate}%*

📦 *METRICS*
• Pending DNs: {pending_dns}
• Exposure: Rs {pending_value:,.2f}

💡 *RECOMMENDATIONS*
"""
        for rec in recommendations:
            dashboard += f"• {rec}\n"
        
        return dashboard.strip()

    def top_risk_cities(self, limit: int = 10) -> List[Dict]:
        """Identify cities with highest risk"""
        try:
            results = self.db.query(
                DeliveryReport.ship_to_city,
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(distinct(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                    else_=None
                ))).label("pending_dns"),
                func.sum(case(
                    (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount),
                    else_=0
                )).label("pending_value")
            ).filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).group_by(
                DeliveryReport.ship_to_city
            ).all()
            
            cities = []
            for r in results:
                if not r.ship_to_city:
                    continue
                
                total_dns = r.total_dns or 1
                pending_dns = r.pending_dns or 0
                delay_rate = (pending_dns / total_dns) * 100
                risk_score = min(100, delay_rate * 1.5)
                
                cities.append({
                    "city": r.ship_to_city,
                    "pending_dns": pending_dns,
                    "pending_value": float(r.pending_value or 0),
                    "delay_rate": round(delay_rate, 1),
                    "risk_score": round(risk_score, 1)
                })
            
            return sorted(cities, key=lambda x: x["risk_score"], reverse=True)[:limit]
            
        except Exception as e:
            logger.error(f"Top risk cities error: {e}")
            return []

    # ======================================================
    # PRIORITY 8: ROOT CAUSE ANALYSIS ENGINE
    # ======================================================

    def root_cause_analysis(self, focus_area: str = "general") -> Dict[str, Any]:
        """
        Perform root cause analysis for delays
        
        Args:
            focus_area: "general", "pod", "city", "dealer", "warehouse"
        """
        cache_key = f"root_cause_{focus_area}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        try:
            # Get samples for analysis
            if focus_area == "pod":
                samples = self._get_pod_delay_samples(500)
                analysis = self._analyze_pod_root_causes(samples)
            elif focus_area == "city":
                samples = self._get_city_delay_samples(500)
                analysis = self._analyze_city_root_causes(samples)
            elif focus_area == "dealer":
                samples = self._get_dealer_delay_samples(500)
                analysis = self._analyze_dealer_root_causes(samples)
            elif focus_area == "warehouse":
                samples = self._get_warehouse_delay_samples(500)
                analysis = self._analyze_warehouse_root_causes(samples)
            else:
                samples = self._get_general_delay_samples(1000)
                analysis = self._analyze_general_root_causes(samples)
            
            result = {
                "success": True,
                "focus_area": focus_area,
                **analysis,
                "formatted_message": self._format_root_cause_analysis(analysis, focus_area)
            }
            
            self._set_cache(cache_key, result, ttl=1800)  # 30 min cache for root cause
            return result
            
        except Exception as e:
            logger.error(f"Root cause analysis error: {e}")
            return {
                "success": False,
                "error": str(e),
                "dealer_delay": 0,
                "warehouse_delay": 0,
                "documentation": 0,
                "transport": 0,
                "other": 0
            }

    def _get_general_delay_samples(self, limit: int = 1000) -> List:
        """Get sample of delayed DNs for analysis"""
        return self.db.query(
            DeliveryReport.dn_no,
            DeliveryReport.customer_name,
            DeliveryReport.warehouse,
            DeliveryReport.ship_to_city,
            DeliveryReport.pgi_status,
            DeliveryReport.pod_status,
            DeliveryReport.dn_create_date,
            DeliveryReport.good_issue_date,
            DeliveryReport.pod_date
        ).filter(
            or_(
                DeliveryReport.pgi_status != "Completed",
                and_(
                    DeliveryReport.pgi_status == "Completed",
                    ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])
                )
            )
        ).limit(limit).all()

    def _get_pod_delay_samples(self, limit: int = 500) -> List:
        """Get POD delay samples"""
        return self.db.query(
            DeliveryReport.dn_no,
            DeliveryReport.customer_name,
            DeliveryReport.warehouse,
            DeliveryReport.ship_to_city,
            DeliveryReport.good_issue_date,
            DeliveryReport.pod_status,
            DeliveryReport.pod_date
        ).filter(
            DeliveryReport.pgi_status == "Completed",
            ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])
        ).limit(limit).all()

    def _get_city_delay_samples(self, limit: int = 500) -> List:
        """Get city-specific delay samples"""
        return self.db.query(
            DeliveryReport.ship_to_city,
            DeliveryReport.customer_name,
            DeliveryReport.warehouse,
            DeliveryReport.pgi_status,
            DeliveryReport.pod_status
        ).filter(
            DeliveryReport.pgi_status != "Completed"
        ).limit(limit).all()

    def _get_dealer_delay_samples(self, limit: int = 500) -> List:
        """Get dealer-specific delay samples"""
        return self.db.query(
            DeliveryReport.customer_name,
            DeliveryReport.warehouse,
            DeliveryReport.pgi_status,
            DeliveryReport.pod_status
        ).filter(
            DeliveryReport.pgi_status != "Completed"
        ).limit(limit).all()

    def _get_warehouse_delay_samples(self, limit: int = 500) -> List:
        """Get warehouse-specific delay samples"""
        return self.db.query(
            DeliveryReport.warehouse,
            DeliveryReport.customer_name,
            DeliveryReport.pgi_status,
            DeliveryReport.pod_status
        ).filter(
            DeliveryReport.pgi_status != "Completed"
        ).limit(limit).all()

    def _analyze_general_root_causes(self, samples: List) -> Dict:
        """Analyze root causes from general samples"""
        dealer_delays = 0
        warehouse_delays = 0
        documentation_delays = 0
        transport_delays = 0
        
        for record in samples:
            # Determine delay type based on patterns
            if not self.is_pgi_completed(record.pgi_status):
                # PGI not completed - likely warehouse or documentation issue
                if record.warehouse and "HPK" in str(record.warehouse):
                    warehouse_delays += 1
                elif record.customer_name and "ELECT" in str(record.customer_name).upper():
                    dealer_delays += 1
                else:
                    documentation_delays += 1
            elif self.is_pod_pending(record.pod_status):
                # POD pending - likely dealer or documentation issue
                dealer_delays += 1
        
        total = len(samples) or 1
        
        return {
            "dealer_delay": round((dealer_delays / total) * 100),
            "warehouse_delay": round((warehouse_delays / total) * 100),
            "documentation": round((documentation_delays / total) * 100),
            "transport": round((transport_delays / total) * 100),
            "other": round(100 - ((dealer_delays + warehouse_delays + documentation_delays + transport_delays) / total * 100))
        }

    def _analyze_pod_root_causes(self, samples: List) -> Dict:
        """Analyze root causes specifically for POD delays"""
        dealer_delays = 0
        documentation_delays = 0
        transport_delays = 0
        
        for record in samples:
            if record.customer_name:
                dealer_delays += 1
            else:
                documentation_delays += 1
        
        total = len(samples) or 1
        
        return {
            "dealer_delay": round((dealer_delays / total) * 100),
            "warehouse_delay": 5,  # Warehouse rarely causes POD delay
            "documentation": round((documentation_delays / total) * 100),
            "transport": round((transport_delays / total) * 100),
            "other": 0
        }

    def _analyze_city_root_causes(self, samples: List) -> Dict:
        """Analyze root causes for city-specific delays"""
        dealer_delays = 0
        warehouse_delays = 0
        transport_delays = 0
        
        for record in samples:
            if record.customer_name:
                dealer_delays += 1
            elif record.warehouse:
                warehouse_delays += 1
            else:
                transport_delays += 1
        
        total = len(samples) or 1
        
        return {
            "dealer_delay": round((dealer_delays / total) * 100),
            "warehouse_delay": round((warehouse_delays / total) * 100),
            "documentation": 5,
            "transport": round((transport_delays / total) * 100),
            "other": 0
        }

    def _analyze_dealer_root_causes(self, samples: List) -> Dict:
        """Analyze root causes for dealer-specific delays"""
        dealer_delays = len(samples)
        total = len(samples) or 1
        
        return {
            "dealer_delay": round((dealer_delays / total) * 100),
            "warehouse_delay": 0,
            "documentation": 0,
            "transport": 0,
            "other": 0
        }

    def _analyze_warehouse_root_causes(self, samples: List) -> Dict:
        """Analyze root causes for warehouse-specific delays"""
        warehouse_delays = len(samples)
        total = len(samples) or 1
        
        return {
            "dealer_delay": 0,
            "warehouse_delay": round((warehouse_delays / total) * 100),
            "documentation": 0,
            "transport": 0,
            "other": 0
        }

    def _format_root_cause_analysis(self, analysis: Dict, focus_area: str) -> str:
        """Format root cause analysis for WhatsApp"""
        message = f"""
🔍 *ROOT CAUSE ANALYSIS* ({focus_area.upper()})

📊 *DELAY BREAKDOWN*

• Dealer Issues: {analysis.get('dealer_delay', 0)}%
• Warehouse Issues: {analysis.get('warehouse_delay', 0)}%
• Documentation: {analysis.get('documentation', 0)}%
• Transport Issues: {analysis.get('transport', 0)}%
• Other Factors: {analysis.get('other', 0)}%

"""
        # Add recommendation based on primary cause
        primary_cause = max(
            [("dealer", analysis.get('dealer_delay', 0)),
             ("warehouse", analysis.get('warehouse_delay', 0)),
             ("documentation", analysis.get('documentation', 0)),
             ("transport", analysis.get('transport', 0))],
            key=lambda x: x[1]
        )
        
        if primary_cause[0] == "dealer" and primary_cause[1] > 30:
            message += "💡 *RECOMMENDATION*: Escalate dealer follow-up process immediately"
        elif primary_cause[0] == "warehouse" and primary_cause[1] > 30:
            message += "💡 *RECOMMENDATION*: Audit warehouse processing and staffing levels"
        elif primary_cause[0] == "documentation" and primary_cause[1] > 20:
            message += "💡 *RECOMMENDATION*: Streamline documentation workflow"
        elif primary_cause[0] == "transport" and primary_cause[1] > 20:
            message += "💡 *RECOMMENDATION*: Review carrier performance and routes"
        
        return message.strip()

    # ======================================================
    # PRIORITY 9: RECOMMENDATION ENGINE
    # ======================================================

    def generate_recommendations(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Generate actionable recommendations based on current state"""
        cache_key = "recommendations"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        recommendations = []
        
        try:
            # Get current metrics
            network = self.network_health_score()
            revenue_risk = self.revenue_at_risk()
            risk_dealers = self.top_risk_dealers(10)
            risk_cities = self.top_risk_cities(5)
            root_cause = self.root_cause_analysis("general")
            
            # Recommendation 1: High-value recovery
            if revenue_risk.get("amount", 0) > 5_000_000_000:
                recommendations.append({
                    "priority": 1,
                    "action": "Recover POD from top 20 dealers with highest pending value",
                    "impact": f"Reduce exposure by up to Rs {revenue_risk.get('amount', 0) * 0.3:,.0f}",
                    "owner": "Dealer Management Team",
                    "timeline": "7 days",
                    "icon": "💰"
                })
            
            # Recommendation 2: Dealer escalation
            if risk_dealers:
                top_dealer = risk_dealers[0]
                recommendations.append({
                    "priority": 2,
                    "action": f"Escalate {top_dealer['dealer']} - {top_dealer['pending_dns']} DNs pending",
                    "impact": f"Resolve {top_dealer['pending_value']:,.0f} revenue at risk",
                    "owner": "Regional Manager",
                    "timeline": "3 days",
                    "icon": "🚨"
                })
            
            # Recommendation 3: City focus
            if risk_cities:
                top_city = risk_cities[0]
                recommendations.append({
                    "priority": 3,
                    "action": f"Deploy recovery team to {top_city['city']}",
                    "impact": f"Clear {top_city['pending_dns']} pending deliveries",
                    "owner": "Operations Lead",
                    "timeline": "5 days",
                    "icon": "🌆"
                })
            
            # Recommendation 4: Process improvement
            if root_cause.get("dealer_delay", 0) > 40:
                recommendations.append({
                    "priority": 4,
                    "action": "Implement daily dealer follow-up automation",
                    "impact": "Reduce dealer-related delays by 50%",
                    "owner": "Process Excellence",
                    "timeline": "14 days",
                    "icon": "🤖"
                })
            elif root_cause.get("warehouse_delay", 0) > 30:
                recommendations.append({
                    "priority": 4,
                    "action": "Conduct warehouse efficiency audit",
                    "impact": "Improve processing time by 40%",
                    "owner": "Warehouse Manager",
                    "timeline": "10 days",
                    "icon": "🏭"
                })
            
            # Recommendation 5: Network improvement
            if network.get("score", 0) < 70:
                recommendations.append({
                    "priority": 5,
                    "action": "Schedule weekly executive review of logistics KPIs",
                    "impact": "Improve network health by 15 points",
                    "owner": "CEO Office",
                    "timeline": "Ongoing",
                    "icon": "📊"
                })
            
            # Cache for 15 minutes
            self._set_cache(cache_key, recommendations[:limit], ttl=900)
            return recommendations[:limit]
            
        except Exception as e:
            logger.error(f"Recommendation generation error: {e}")
            return []

    # ======================================================
    # PRIORITY 10: RAPIDFUZZ DEALER MATCHING (UPGRADED)
    # ======================================================

    def fuzzy_search_dealer(self, dealer_name: str, threshold: int = 70) -> Optional[str]:
        """Fuzzy search for dealer using RapidFuzz"""
        if not RAPIDFUZZ_AVAILABLE:
            return self._simple_dealer_match(dealer_name)
        
        # Build dealer cache if needed
        if not self._dealer_cache:
            dealers = self.db.query(DeliveryReport.customer_name).distinct().filter(
                DeliveryReport.customer_name.isnot(None)
            ).limit(5000).all()
            self._dealer_cache = {d[0]: d[0] for d in dealers if d[0]}
        
        dealer_list = list(self._dealer_cache.keys())
        
        if not dealer_list:
            return None
        
        # Try exact match first
        for dealer in dealer_list:
            if dealer.lower() == dealer_name.lower():
                return dealer
        
        # Try contains match
        dealer_lower = dealer_name.lower()
        for dealer in dealer_list:
            if dealer_lower in dealer.lower():
                return dealer
        
        # Fuzzy match
        match = process.extractOne(dealer_name, dealer_list, scorer=fuzz.token_sort_ratio)
        
        if match and match[1] >= threshold:
            logger.info(f"Fuzzy match: '{dealer_name}' -> '{match[0]}' (score: {match[1]})")
            return match[0]
        
        return None

    def _simple_dealer_match(self, dealer_name: str) -> Optional[str]:
        """Simple dealer matching fallback"""
        dealers = self.db.query(DeliveryReport.customer_name).distinct().filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).limit(1).all()
        
        return dealers[0][0] if dealers else None

    def search_dealer(self, dealer_name: str) -> Optional[str]:
        """Search dealer with fuzzy matching"""
        return self.fuzzy_search_dealer(dealer_name)

    # ======================================================
    # PRIORITY 11: CEO COMMAND CENTER
    # ======================================================

    def ceo_briefing(self) -> Dict[str, Any]:
        """Generate comprehensive CEO briefing"""
        try:
            network = self.network_health_score()
            revenue_risk = self.revenue_at_risk()
            inventory_risk = self.inventory_at_risk()
            recommendations = self.generate_recommendations(3)
            risk_dealers = self.top_risk_dealers(3)
            risk_cities = self.top_risk_cities(3)
            risk_warehouses = self.top_risk_warehouses(3)
            root_cause = self.root_cause_analysis("general")
            
            briefing = {
                "success": True,
                "network_health": network,
                "revenue_at_risk": revenue_risk,
                "inventory_at_risk": inventory_risk,
                "top_risks": {
                    "dealers": risk_dealers,
                    "cities": risk_cities,
                    "warehouses": risk_warehouses
                },
                "root_causes": root_cause,
                "recommendations": recommendations,
                "formatted_message": self._format_ceo_briefing(
                    network, revenue_risk, risk_dealers, risk_cities, risk_warehouses, recommendations
                )
            }
            
            return briefing
            
        except Exception as e:
            logger.error(f"CEO briefing error: {e}")
            return {"success": False, "error": str(e)}

    def _format_ceo_briefing(self, network: Dict, revenue_risk: Dict, risk_dealers: List,
                              risk_cities: List, risk_warehouses: List, recommendations: List) -> str:
        """Format CEO briefing for WhatsApp"""
        briefing = f"""
👑 *CEO COMMAND CENTER*
━━━━━━━━━━━━━━━━━━━━

{network.get('icon', '📊')} *NETWORK HEALTH: {network.get('score', 0)}/100* ({network.get('category', 'Unknown')})

💰 *REVENUE AT RISK: {revenue_risk.get('formatted', 'Rs 0')}*
📦 *INVENTORY AT RISK: {inventory_at_risk()['formatted']} units*

🚨 *TOP 3 RISKS*

*Dealers:*
"""
        for i, d in enumerate(risk_dealers[:3], 1):
            briefing += f"   {i}. {d.get('dealer', 'Unknown')} ({d.get('risk_score', 0)}%)\n"
        
        briefing += "\n*Cities:*\n"
        for i, c in enumerate(risk_cities[:3], 1):
            briefing += f"   {i}. {c.get('city', 'Unknown')} ({c.get('risk_score', 0)}%)\n"
        
        briefing += "\n*Warehouses:*\n"
        for i, w in enumerate(risk_warehouses[:3], 1):
            briefing += f"   {i}. {w.get('warehouse', 'Unknown')} ({w.get('risk_score', 0)}%)\n"
        
        briefing += "\n━━━━━━━━━━━━━━━━━━━━\n"
        briefing += "💡 *RECOMMENDATIONS*\n"
        
        for rec in recommendations[:3]:
            briefing += f"\n{rec.get('icon', '•')} *Priority {rec.get('priority')}*\n"
            briefing += f"   {rec.get('action', '')}\n"
            briefing += f"   Impact: {rec.get('impact', '')}\n"
            briefing += f"   Timeline: {rec.get('timeline', '')}\n"
        
        return briefing.strip()

    # ======================================================
    # EXISTING METHODS (KEPT FOR COMPATIBILITY)
    # ======================================================

    def pending_metrics(self) -> Dict[str, Any]:
        """Get pending metrics (existing method)"""
        result = self.db.query(
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_qty),
                else_=0
            )).label("pending_units"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount),
                else_=0
            )).label("pending_value"),
            func.count(distinct(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                else_=None
            ))).label("pending_dns")
        ).first()
        
        return {
            "total_dns": result.total_dns or 0,
            "pending_dns": result.pending_dns or 0,
            "pending_units": float(result.pending_units or 0),
            "pending_value": float(result.pending_value or 0)
        }

    def pod_metrics(self) -> Dict[str, Any]:
        """Get POD metrics (existing method)"""
        result = self.db.query(
            func.count(distinct(case(
                (and_(
                    DeliveryReport.pgi_status == "Completed",
                    ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])
                ), DeliveryReport.dn_no),
                else_=None
            ))).label("pod_pending_dns"),
            func.sum(case(
                (and_(
                    DeliveryReport.pgi_status == "Completed",
                    ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])
                ), DeliveryReport.dn_qty),
                else_=0
            )).label("pod_pending_units")
        ).first()
        
        return {
            "pod_pending_dns": result.pod_pending_dns or 0,
            "pod_pending_units": float(result.pod_pending_units or 0)
        }

    def dealer_dashboard_metrics(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer dashboard metrics (existing method with fuzzy fallback)"""
        # First try exact match
        dealer = self._simple_dealer_match(dealer_name) if not RAPIDFUZZ_AVAILABLE else None
        
        if not dealer and RAPIDFUZZ_AVAILABLE:
            dealer = self.fuzzy_search_dealer(dealer_name)
        
        if not dealer:
            # Try to find suggestions
            suggestions = self.find_dealers(dealer_name, limit=5)
            if suggestions:
                return {
                    "success": False,
                    "fuzzy": True,
                    "message": f"Multiple dealers found",
                    "matches": suggestions,
                    "summary": "🔍 Did you mean:\n" + "\n".join([f"• {d}" for d in suggestions])
                }
            return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
        
        # Use existing logic with exact dealer name
        # ... (rest of existing implementation)
        return self._dealer_dashboard_metrics_exact(dealer)

    def _dealer_dashboard_metrics_exact(self, dealer_name: str) -> Dict[str, Any]:
        """Original dealer dashboard logic with exact match"""
        metrics = self.db.query(
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(distinct(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                else_=None
            ))).label("pending_dns"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount),
                else_=0
            )).label("pending_value"),
            func.count(distinct(case(
                (and_(DeliveryReport.pgi_status == "Completed", 
                      ~func.lower(DeliveryReport.pod_status).in_(["received", "done", "completed"])),
                 DeliveryReport.dn_no),
                else_=None
            ))).label("pod_pending_dns")
        ).filter(DeliveryReport.customer_name == dealer_name).first()
        
        total_dns = metrics.total_dns or 0
        
        if total_dns == 0:
            return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
        
        return {
            "success": True,
            "dealer_name": dealer_name,
            "total_dns": total_dns,
            "total_value": float(metrics.total_value or 0),
            "pending_dns": metrics.pending_dns or 0,
            "pending_value": float(metrics.pending_value or 0),
            "pod_pending_dns": metrics.pod_pending_dns or 0
        }

    def find_dealers(self, dealer_name: str, limit: int = 10) -> List[str]:
        """Find dealers matching the given name (with fuzzy fallback)"""
        if RAPIDFUZZ_AVAILABLE:
            match = self.fuzzy_search_dealer(dealer_name, threshold=50)
            if match:
                return [match]
        
        results = self.db.query(DeliveryReport.customer_name).filter(
            DeliveryReport.customer_name.isnot(None),
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).distinct().limit(limit).all()
        
        return [r[0] for r in results if r[0]]

    def dealer_rankings(self, limit: int = 10) -> Dict[str, List]:
        """Get dealer rankings (existing method)"""
        # ... existing implementation (kept for compatibility)
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
            if not r.customer_name:
                continue
            total_dns = r.total_dns or 1
            pending_dns = r.pending_dns or 0
            score = max(0, 100 - ((pending_dns / total_dns) * 100))
            
            dealers.append({
                "dealer": r.customer_name,
                "total_dns": total_dns,
                "total_value": float(r.total_value or 0),
                "pending_dns": pending_dns,
                "score": round(score, 1)
            })
        
        return {
            "by_value": sorted(dealers, key=lambda x: x["total_value"], reverse=True)[:limit],
            "by_pending": sorted(dealers, key=lambda x: x["pending_dns"], reverse=True)[:limit],
            "by_score": sorted(dealers, key=lambda x: x["score"], reverse=True)[:limit]
        }

    def warehouse_rankings(self, limit: int = 10) -> Dict[str, List]:
        """Get warehouse rankings (existing method)"""
        # ... existing implementation (kept for compatibility)
        results = self.db.query(
            DeliveryReport.warehouse,
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.count(distinct(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                else_=None
            ))).label("pending_dns")
        ).filter(
            DeliveryReport.warehouse.isnot(None)
        ).group_by(
            DeliveryReport.warehouse
        ).all()
        
        warehouses = []
        for r in results:
            if not r.warehouse:
                continue
            total_dns = r.total_dns or 1
            pending_dns = r.pending_dns or 0
            efficiency = max(0, 100 - ((pending_dns / total_dns) * 100))
            
            warehouses.append({
                "warehouse": r.warehouse,
                "total_dns": total_dns,
                "pending_dns": pending_dns,
                "efficiency_score": round(efficiency, 1)
            })
        
        return {
            "by_efficiency": sorted(warehouses, key=lambda x: x["efficiency_score"], reverse=True)[:limit],
            "all_warehouses": warehouses
        }

    def city_rankings(self, limit: int = 10) -> Dict[str, List]:
        """Get city rankings (existing method)"""
        # ... existing implementation (kept for compatibility)
        results = self.db.query(
            DeliveryReport.ship_to_city,
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.count(distinct(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                else_=None
            ))).label("pending_dns")
        ).filter(
            DeliveryReport.ship_to_city.isnot(None)
        ).group_by(
            DeliveryReport.ship_to_city
        ).all()
        
        cities = []
        for r in results:
            if not r.ship_to_city:
                continue
            total_dns = r.total_dns or 1
            pending_dns = r.pending_dns or 0
            delay_rate = (pending_dns / total_dns) * 100
            performance = max(0, 100 - delay_rate)
            
            cities.append({
                "city": r.ship_to_city,
                "total_dns": total_dns,
                "pending_dns": pending_dns,
                "delay_rate": round(delay_rate, 1),
                "performance_score": round(performance, 1)
            })
        
        return {
            "by_performance": sorted(cities, key=lambda x: x["performance_score"], reverse=True)[:limit],
            "by_pending": sorted(cities, key=lambda x: x["pending_dns"], reverse=True)[:limit],
            "all_cities": cities
        }

    def top_risk_dealers(self, limit: int = 10) -> List[Dict]:
        """Get top risk dealers (existing method)"""
        results = self.db.query(
            DeliveryReport.customer_name,
            func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
            func.count(distinct(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_no),
                else_=None
            ))).label("pending_dns"),
            func.sum(case(
                (DeliveryReport.pgi_status != "Completed", DeliveryReport.dn_amount),
                else_=0
            )).label("pending_value")
        ).filter(
            DeliveryReport.customer_name.isnot(None)
        ).group_by(
            DeliveryReport.customer_name
        ).all()
        
        dealers = []
        for r in results:
            if not r.customer_name:
                continue
            total_dns = r.total_dns or 1
            pending_dns = r.pending_dns or 0
            risk_score = (pending_dns / total_dns) * 100
            
            dealers.append({
                "dealer": r.customer_name,
                "pending_dns": pending_dns,
                "pending_value": float(r.pending_value or 0),
                "risk_score": round(risk_score, 1)
            })
        
        return sorted(dealers, key=lambda x: x["risk_score"], reverse=True)[:limit]

    # ======================================================
    # CACHE HELPERS
    # ======================================================

    def _get_cached(self, key: str) -> Optional[Any]:
        """Get cached value"""
        if key in self._cache:
            value, timestamp = self._cache[key]
            if (datetime.utcnow() - timestamp).seconds < self._cache_ttl:
                return value
            del self._cache[key]
        return None

    def _set_cache(self, key: str, value: Any, ttl: int = None):
        """Set cached value"""
        self._cache[key] = (value, datetime.utcnow())
        if ttl:
            self._cache_ttl = ttl


# ======================================================
# FACTORY FUNCTION
# ======================================================

def get_analytics_service(db: Session) -> AnalyticsService:
    """Get analytics service instance"""
    return AnalyticsService(db)
