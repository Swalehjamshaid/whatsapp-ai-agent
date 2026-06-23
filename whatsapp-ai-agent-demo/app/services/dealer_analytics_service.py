# ==========================================================
# FILE: app/services/dealer_analytics_service.py
# PURPOSE: Dealer 360° Analytics & Dashboard Engine
# VERSION: 3.0 - FIXED: Dealer Master Join + COALESCE + No MAX() on metadata
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
import math
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, case, desc, asc, distinct, extract, text

# ==========================================================
# IMPORTS
# ==========================================================

from app.models import DeliveryReport
# Import DealerMaster model if it exists
try:
    from app.models import DealerMaster
    HAS_DEALER_MASTER = True
except ImportError:
    HAS_DEALER_MASTER = False
    logger.warning("⚠️ DealerMaster model not found - using fallback")

from app.services.distance_service import get_distance_service
from app.services.analytics_service import KPIEngine, EntityResolver, SearchEngine

# ==========================================================
# CONSTANTS
# ==========================================================

DISTANCE_CATEGORIES = {
    "Local": (0, 50),
    "Regional": (50, 200),
    "Remote": (200, float('inf'))
}

RISK_LEVELS = {
    "Critical": (0, 25),
    "High": (25, 50),
    "Medium": (50, 75),
    "Low": (75, 101)
}

PERFORMANCE_GRADES = {
    "A+": (95, 101),
    "A": (85, 95),
    "B+": (75, 85),
    "B": (65, 75),
    "C": (50, 65),
    "D": (0, 50)
}


# ==========================================================
# BLOCK 1: DEALER 360° DASHBOARD CLASS
# ==========================================================
# ==========================================================
# BLOCK 1: DEALER 360° DASHBOARD CLASS
# ==========================================================

class Dealer360Dashboard:
    """
    Complete Dealer 360° Dashboard with all analytics.
    
    Sections:
    1. Dealer Profile (FIXED - Uses DeliveryReport only)
    2. Business Volume
    3. Delivery Status
    4. POD Status
    5. PGI Status
    6. Performance KPIs
    7. Distance Analytics
    8. Product Analytics
    9. City Analytics
    10. Aging Analytics
    11. Control Tower Alerts
    12. Executive Summary
    13. Management Insights
    """
    
    def __init__(self, db: Session, resolver: EntityResolver, search: SearchEngine):
        self.db = db
        self.resolver = resolver
        self.search = search
        self.distance_service = get_distance_service()
# ==========================================================
# BLOCK 2.5: GET DEALER PROFILE FROM MASTER TABLE
# ==========================================================
# ==========================================================
# BLOCK 2.5: GET DEALER PROFILE FROM DELIVERY REPORT
# ==========================================================

    def _get_dealer_profile(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get dealer profile from DeliveryReport table.
        FIXED: Uses DeliveryReport instead of DealerMaster.
        """
        profile = {
            "dealer_code": 'N/A',
            "customer_code": 'N/A',
            "division": 'N/A',
            "sales_office": 'N/A',
            "sales_manager": 'N/A',
            "warehouse": 'N/A',
            "city": 'N/A',
            "region": 'N/A'
        }
        
        try:
            # Query DeliveryReport for dealer info
            result = self.db.query(
                func.max(DeliveryReport.dealer_code).label("dealer_code"),
                func.max(DeliveryReport.customer_code).label("customer_code"),
                func.max(DeliveryReport.division).label("division"),
                func.max(DeliveryReport.sales_office).label("sales_office"),
                func.max(DeliveryReport.sales_manager).label("sales_manager"),
                func.max(DeliveryReport.warehouse).label("warehouse"),
                func.max(DeliveryReport.ship_to_city).label("city")
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name)
            ).first()
            
            if result:
                profile = {
                    "dealer_code": result.dealer_code or 'N/A',
                    "customer_code": result.customer_code or 'N/A',
                    "division": result.division or 'N/A',
                    "sales_office": result.sales_office or 'N/A',
                    "sales_manager": result.sales_manager or 'N/A',
                    "warehouse": result.warehouse or 'N/A',
                    "city": result.city or 'N/A',
                    "region": 'N/A'
                }
                logger.info(f"✅ Found dealer profile from DeliveryReport: {profile['dealer_code']}")
            else:
                logger.warning(f"⚠️ No profile found for dealer: {dealer_name}")
                    
        except Exception as e:
            logger.error(f"Error querying DeliveryReport for dealer profile: {e}")
        
        return profile
# ==========================================================
# BLOCK 3: DEALER PROFILE
# ==========================================================
# BLOCK 3: DEALER PROFILE
# ==========================================================

    def _build_profile(self, dealer_name: str, data: Dict) -> Dict[str, Any]:
        """Build dealer profile section - FIXED: Uses master table data."""
        first_dn = data.get('first_dn_date')
        latest_dn = data.get('latest_dn_date')
        
        profile = {
            "dealer_name": dealer_name,
            # These now come from master table via _get_dealer_profile
            "dealer_code": data.get('dealer_code', 'N/A'),
            "customer_code": data.get('customer_code', 'N/A'),
            "division": data.get('division', 'N/A'),
            "sales_office": data.get('sales_office', 'N/A'),
            "sales_manager": data.get('sales_manager', 'N/A'),
            "warehouse": data.get('warehouse', 'N/A'),
            "city": data.get('city', 'N/A'),
            "region": data.get('region', 'N/A'),
            "first_dn_date": first_dn.isoformat() if first_dn else 'N/A',
            "latest_dn_date": latest_dn.isoformat() if latest_dn else 'N/A',
            "total_active_days": self._calculate_active_days(first_dn, latest_dn),
        }
        
        return profile

    

# ==========================================================
# BLOCK 4: BUSINESS VOLUME
# ==========================================================

    def _build_business_volume(self, data: Dict) -> Dict[str, Any]:
        """Build business volume section."""
        total_dns = data.get('total_dns', 0)
        total_units = data.get('total_units', 0)
        total_revenue = data.get('total_revenue', 0)
        
        return {
            "total_dns": total_dns,
            "total_units": total_units,
            "total_revenue": round(total_revenue, 0),
            "avg_revenue_per_dn": round(total_revenue / total_dns, 0) if total_dns > 0 else 0,
            "avg_revenue_per_unit": round(total_revenue / total_units, 0) if total_units > 0 else 0,
            "highest_value_dn": data.get('highest_dn', {}),
            "lowest_value_dn": data.get('lowest_dn', {})
        }


# ==========================================================
# BLOCK 5: DELIVERY STATUS
# ==========================================================

    def _build_delivery_status(self, data: Dict) -> Dict[str, Any]:
        """
        Build delivery status section.
        
        Rules:
        - Pending PGI: PGI not completed
        - In Transit: PGI completed, POD not received
        - Delivered: POD received
        - Total Must Equal Total DNs
        """
        pending_pgi = data.get('pending_pgi_dns', 0)
        in_transit = data.get('transit_dns', 0)
        delivered = data.get('delivered_dns', 0)
        total_dns = data.get('total_dns', 0)
        
        # Calculate percentages
        pending_pgi_pct = round(pending_pgi / total_dns * 100, 1) if total_dns > 0 else 0
        in_transit_pct = round(in_transit / total_dns * 100, 1) if total_dns > 0 else 0
        delivered_pct = round(delivered / total_dns * 100, 1) if total_dns > 0 else 0
        
        # Validate: Pending PGI + In Transit + Delivered = Total DNs
        sum_check = pending_pgi + in_transit + delivered
        is_valid = sum_check == total_dns
        
        if not is_valid:
            logger.warning(f"⚠️ Delivery status sum mismatch: {sum_check} != {total_dns}")
        
        return {
            "pending_pgi": pending_pgi,
            "in_transit": in_transit,
            "delivered": delivered,
            "total": total_dns,
            "pending_pgi_percent": pending_pgi_pct,
            "in_transit_percent": in_transit_pct,
            "delivered_percent": delivered_pct,
            "status_buckets_valid": is_valid,
            "status_buckets_summary": f"{pending_pgi} + {in_transit} + {delivered} = {sum_check} {'✅' if is_valid else '⚠️'}"
        }


# ==========================================================
# BLOCK 6: POD STATUS
# ==========================================================

    def _build_pod_status(self, data: Dict) -> Dict[str, Any]:
        """Build POD status section."""
        pod_completed = data.get('pod_completed_dns', 0)
        pending_pod = data.get('pending_pod_dns', 0)
        delivered = data.get('delivered_dns', 0)
        
        pod_compliance = round(pod_completed / delivered * 100, 1) if delivered > 0 else 0
        
        # Ensure POD compliance doesn't exceed 100%
        pod_compliance = min(pod_compliance, 100.0)
        
        return {
            "pod_completed": pod_completed,
            "pending_pod": pending_pod,
            "pod_compliance": pod_compliance,
            "avg_pod_days": data.get('avg_pod_days', 0),
            "oldest_pending_pod": data.get('oldest_pending_pod', 'N/A')
        }


# ==========================================================
# BLOCK 7: PGI STATUS
# ==========================================================

    def _build_pgi_status(self, data: Dict) -> Dict[str, Any]:
        """Build PGI status section."""
        pgi_completed = data.get('pgi_completed_dns', 0)
        pending_pgi = data.get('pending_pgi_dns', 0)
        total_dns = data.get('total_dns', 0)
        
        pgi_compliance = round(pgi_completed / total_dns * 100, 1) if total_dns > 0 else 0
        
        # Ensure PGI compliance doesn't exceed 100%
        pgi_compliance = min(pgi_compliance, 100.0)
        
        return {
            "pgi_completed": pgi_completed,
            "pending_pgi": pending_pgi,
            "pgi_compliance": pgi_compliance,
            "avg_pgi_days": data.get('avg_pgi_days', 0),
            "oldest_pending_pgi": data.get('oldest_pending_pgi', 'N/A')
        }


# ==========================================================
# BLOCK 8: PERFORMANCE KPIs
# ==========================================================

    def _build_performance(self, data: Dict) -> Dict[str, Any]:
        """Build performance KPIs section."""
        total_dns = data.get('total_dns', 0)
        delivered = data.get('delivered_dns', 0)
        transit = data.get('transit_dns', 0)
        pod_completed = data.get('pod_completed_dns', 0)
        
        # Calculate rates (capped at 100%)
        delivery_rate = min(KPIEngine.calculate_delivery_rate(delivered, total_dns), 100.0)
        pgi_rate = min(KPIEngine.calculate_pgi_rate(delivered, transit, total_dns), 100.0)
        pod_rate = min(KPIEngine.calculate_pod_rate(pod_completed, delivered), 100.0) if delivered > 0 else 0
        
        health_score = KPIEngine.calculate_health_score({
            "delivery_rate": delivery_rate,
            "pod_rate": pod_rate,
            "avg_aging": 0,
            "revenue": data.get('total_revenue', 0)
        })
        
        risk_level, risk_score = KPIEngine.calculate_risk_level(delivery_rate, pod_rate, 0)
        
        return {
            "delivery_rate": delivery_rate,
            "pgi_rate": pgi_rate,
            "pod_rate": pod_rate,
            "health_score": health_score,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "performance_grade": self._get_performance_grade(health_score)
        }
    
    def _get_performance_grade(self, health_score: int) -> str:
        """Get performance grade based on health score."""
        for grade, (min_score, max_score) in PERFORMANCE_GRADES.items():
            if min_score <= health_score < max_score:
                return grade
        return "D"


# ==========================================================
# BLOCK 9: DISTANCE ANALYTICS
# ==========================================================

    def _build_distance(self, data: Dict) -> Dict[str, Any]:
        """Build distance analytics section."""
        warehouse = data.get('warehouse')
        city = data.get('city')
        
        result = {
            "warehouse": warehouse or 'N/A',
            "dealer_city": city or 'N/A',
            "air_distance": None,
            "road_distance": None,
            "driving_time": None,
            "distance_category": 'N/A'
        }
        
        if warehouse and city:
            try:
                distance_info = self.distance_service.calculate_warehouse_distance(warehouse, city)
                if distance_info and distance_info.get('success'):
                    result['air_distance'] = distance_info.get('distance_km')
                    result['road_distance'] = distance_info.get('distance_km')
                    result['driving_time'] = distance_info.get('approx_driving_hours')
                    result['distance_category'] = self._get_distance_category(distance_info.get('distance_km', 0))
            except Exception as e:
                logger.error(f"Distance error: {e}")
        
        return result
    
    def _get_distance_category(self, distance_km: float) -> str:
        """Get distance category based on distance."""
        for category, (min_dist, max_dist) in DISTANCE_CATEGORIES.items():
            if min_dist <= distance_km < max_dist:
                return category
        return "Remote"


# ==========================================================
# BLOCK 10: PRODUCT ANALYTICS
# ==========================================================

    def _build_product_analytics(self, dealer_name: str) -> Dict[str, Any]:
        """Build product analytics section."""
        try:
            # Get all products for this dealer
            products = self.db.query(
                DeliveryReport.customer_model,
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count')
            ).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.customer_model.isnot(None),
                DeliveryReport.customer_model != ''
            ).group_by(
                DeliveryReport.customer_model
            ).order_by(
                desc('total_revenue')
            ).all()
            
            if not products:
                return {
                    "total_products": 0,
                    "top_product": "N/A",
                    "top_5_products": [],
                    "highest_revenue_product": "N/A",
                    "highest_volume_product": "N/A",
                    "product_mix": {}
                }
            
            total_revenue = sum(p.total_revenue or 0 for p in products)
            total_units = sum(p.total_units or 0 for p in products)
            
            top_products = []
            for p in products[:5]:
                revenue = p.total_revenue or 0
                units = p.total_units or 0
                dns = p.dn_count or 0
                revenue_share = round(revenue / total_revenue * 100, 1) if total_revenue > 0 else 0
                
                top_products.append({
                    "product": p.customer_model,
                    "revenue": round(revenue, 0),
                    "units": int(units),
                    "dns": int(dns),
                    "revenue_share": revenue_share
                })
            
            # Find highest revenue and highest volume products
            highest_revenue = max(products, key=lambda x: x.total_revenue or 0) if products else None
            highest_volume = max(products, key=lambda x: x.total_units or 0) if products else None
            
            return {
                "total_products": len(products),
                "top_product": products[0].customer_model if products else "N/A",
                "top_5_products": top_products,
                "highest_revenue_product": highest_revenue.customer_model if highest_revenue else "N/A",
                "highest_volume_product": highest_volume.customer_model if highest_volume else "N/A",
                "product_mix": {
                    "total_revenue": round(total_revenue, 0),
                    "total_units": int(total_units)
                }
            }
            
        except Exception as e:
            logger.error(f"Product analytics error: {e}")
            return {
                "total_products": 0,
                "top_product": "N/A",
                "top_5_products": [],
                "highest_revenue_product": "N/A",
                "highest_volume_product": "N/A",
                "product_mix": {}
            }


# ==========================================================
# BLOCK 11: CITY ANALYTICS
# ==========================================================

    def _build_city_analytics(self, dealer_name: str) -> Dict[str, Any]:
        """Build city analytics section."""
        try:
            cities = self.db.query(
                DeliveryReport.ship_to_city,
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count')
            ).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.ship_to_city.isnot(None),
                DeliveryReport.ship_to_city != ''
            ).group_by(
                DeliveryReport.ship_to_city
            ).order_by(
                desc('total_revenue')
            ).all()
            
            if not cities:
                return {
                    "cities_served": 0,
                    "top_city": "N/A",
                    "revenue_by_city": {},
                    "units_by_city": {}
                }
            
            revenue_by_city = {}
            units_by_city = {}
            
            for city in cities:
                city_name = city.ship_to_city
                revenue_by_city[city_name] = round(city.total_revenue or 0, 0)
                units_by_city[city_name] = int(city.total_units or 0)
            
            return {
                "cities_served": len(cities),
                "top_city": cities[0].ship_to_city if cities else "N/A",
                "revenue_by_city": revenue_by_city,
                "units_by_city": units_by_city
            }
            
        except Exception as e:
            logger.error(f"City analytics error: {e}")
            return {
                "cities_served": 0,
                "top_city": "N/A",
                "revenue_by_city": {},
                "units_by_city": {}
            }


# ==========================================================
# BLOCK 12: AGING ANALYTICS
# ==========================================================

    def _build_aging_analytics(self, dealer_name: str) -> Dict[str, Any]:
        """Build aging analytics section."""
        try:
            # Query aging data using SQLAlchemy extract
            result = self.db.query(
                func.avg(
                    func.extract('day', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                ).label('avg_delivery_days'),
                func.avg(
                    func.extract('day', DeliveryReport.pod_date - DeliveryReport.good_issue_date)
                ).label('avg_pod_days'),
                func.avg(
                    func.extract('day', DeliveryReport.pod_date - DeliveryReport.dn_create_date)
                ).label('avg_cycle_days'),
                func.max(
                    func.extract('day', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                ).label('max_delivery_days'),
                func.max(
                    func.extract('day', DeliveryReport.pod_date - DeliveryReport.good_issue_date)
                ).label('max_pod_days')
            ).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.dn_create_date.isnot(None)
            ).first()
            
            return {
                "avg_delivery_days": round(result.avg_delivery_days or 0, 1) if result else 0,
                "avg_pod_days": round(result.avg_pod_days or 0, 1) if result else 0,
                "avg_cycle_days": round(result.avg_cycle_days or 0, 1) if result else 0,
                "max_delivery_days": int(result.max_delivery_days or 0) if result else 0,
                "max_pod_days": int(result.max_pod_days or 0) if result else 0
            }
            
        except Exception as e:
            logger.error(f"Aging analytics error: {e}")
            return {
                "avg_delivery_days": 0,
                "avg_pod_days": 0,
                "avg_cycle_days": 0,
                "max_delivery_days": 0,
                "max_pod_days": 0
            }


# ==========================================================
# BLOCK 13: CONTROL TOWER ALERTS
# ==========================================================

    def _build_alerts(self, dashboard: Dict) -> List[Dict[str, Any]]:
        """Build control tower alerts."""
        alerts = []
        performance = dashboard.get('performance', {})
        delivery_status = dashboard.get('delivery_status', {})
        pod_status = dashboard.get('pod_status', {})
        pgi_status = dashboard.get('pgi_status', {})
        distance = dashboard.get('distance', {})
        
        # Alert 1: High Pending PGI
        pending_pgi = delivery_status.get('pending_pgi', 0)
        if pending_pgi > 5:
            alerts.append({
                "type": "High Pending PGI",
                "severity": "high" if pending_pgi > 20 else "medium",
                "message": f"{pending_pgi} DNs pending PGI",
                "recommendation": "Process PGI for pending deliveries"
            })
        
        # Alert 2: High Pending POD
        pending_pod = pod_status.get('pending_pod', 0)
        if pending_pod > 5:
            alerts.append({
                "type": "High Pending POD",
                "severity": "critical" if pending_pod > 30 else "high",
                "message": f"{pending_pod} DNs pending POD",
                "recommendation": "Collect POD for delivered items"
            })
        
        # Alert 3: Low Delivery Performance
        delivery_rate = performance.get('delivery_rate', 0)
        if delivery_rate < 70:
            alerts.append({
                "type": "Low Delivery Performance",
                "severity": "high" if delivery_rate < 50 else "medium",
                "message": f"Delivery rate is {delivery_rate}%",
                "recommendation": "Investigate delivery delays"
            })
        
        # Alert 4: Low POD Compliance
        pod_compliance = pod_status.get('pod_compliance', 0)
        if pod_compliance < 70:
            alerts.append({
                "type": "Low POD Compliance",
                "severity": "high" if pod_compliance < 50 else "medium",
                "message": f"POD compliance is {pod_compliance}%",
                "recommendation": "Improve POD collection process"
            })
        
        # Alert 5: High PGI Compliance Issue
        pgi_compliance = pgi_status.get('pgi_compliance', 0)
        if pgi_compliance < 70:
            alerts.append({
                "type": "Low PGI Compliance",
                "severity": "medium",
                "message": f"PGI compliance is {pgi_compliance}%",
                "recommendation": "Improve PGI processing"
            })
        
        # Alert 6: High Risk Dealer
        risk_level = performance.get('risk_level', 'Low')
        if risk_level in ['High', 'Critical']:
            alerts.append({
                "type": "High Risk Dealer",
                "severity": "critical" if risk_level == 'Critical' else "high",
                "message": f"Dealer has {risk_level} risk profile",
                "recommendation": "Review dealer performance and take action"
            })
        
        # Alert 7: Distance Risk
        distance_category = distance.get('distance_category', 'Local')
        if distance_category == 'Remote':
            alerts.append({
                "type": "Distance Risk",
                "severity": "medium",
                "message": "Dealer is in remote location",
                "recommendation": "Consider logistics optimization"
            })
        
        # Sort alerts by severity
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        alerts.sort(key=lambda x: severity_order.get(x.get('severity', 'low'), 4))
        
        return alerts


# ==========================================================
# BLOCK 14: EXECUTIVE SUMMARY
# ==========================================================

    def _build_executive_summary(self, dashboard: Dict) -> str:
        """Build executive summary."""
        profile = dashboard.get('profile', {})
        business = dashboard.get('business_volume', {})
        performance = dashboard.get('performance', {})
        delivery_status = dashboard.get('delivery_status', {})
        pod_status = dashboard.get('pod_status', {})
        alerts = dashboard.get('alerts', [])
        
        dealer_name = profile.get('dealer_name', 'Dealer')
        total_revenue = business.get('total_revenue', 0)
        total_dns = business.get('total_dns', 0)
        delivery_rate = performance.get('delivery_rate', 0)
        pod_compliance = pod_status.get('pod_compliance', 0)
        pending_pgi = delivery_status.get('pending_pgi', 0)
        pending_pod = pod_status.get('pending_pod', 0)
        risk_level = performance.get('risk_level', 'Unknown')
        
        summary_lines = [
            f"🏢 {dealer_name} generated PKR {total_revenue:,.0f} revenue.",
            f"📦 Total DNs: {total_dns}",
            f"🚚 Delivery Rate: {delivery_rate}%",
            f"📋 POD Compliance: {pod_compliance}%",
            f"⏳ Pending PGI: {pending_pgi}",
            f"⏳ Pending POD: {pending_pod}",
            f"⚠️ Risk Level: {risk_level}",
        ]
        
        # Recommended action based on alerts
        if alerts:
            critical_alerts = [a for a in alerts if a.get('severity') == 'critical']
            high_alerts = [a for a in alerts if a.get('severity') == 'high']
            
            if critical_alerts:
                summary_lines.append(f"🚨 CRITICAL: {len(critical_alerts)} Critical Alert(s) - Immediate Action Required")
                for alert in critical_alerts[:2]:
                    summary_lines.append(f"   • {alert.get('recommendation', 'Take immediate action')}")
            elif high_alerts:
                summary_lines.append(f"⚠️ HIGH: {len(high_alerts)} High Alert(s) - Review Required")
                for alert in high_alerts[:2]:
                    summary_lines.append(f"   • {alert.get('recommendation', 'Review and take action')}")
            else:
                summary_lines.append("✅ No Critical Alerts - Dealer is performing well")
                if delivery_rate > 90 and pod_compliance > 90:
                    summary_lines.append("📈 Excellent performance - Maintain current operations")
                elif delivery_rate > 80:
                    summary_lines.append("📈 Good performance - Focus on improving POD compliance")
        else:
            summary_lines.append("✅ No Alerts - Dealer is performing well")
        
        return "\n".join(summary_lines)


# ==========================================================
# BLOCK 15: MANAGEMENT INSIGHTS
# ==========================================================

    def _build_insights(self, dashboard: Dict) -> Dict[str, str]:
        """Build management insights."""
        performance = dashboard.get('performance', {})
        delivery_status = dashboard.get('delivery_status', {})
        pod_status = dashboard.get('pod_status', {})
        business = dashboard.get('business_volume', {})
        alerts = dashboard.get('alerts', [])
        distance = dashboard.get('distance', {})
        
        delivery_rate = performance.get('delivery_rate', 0)
        pod_compliance = pod_status.get('pod_compliance', 0)
        total_revenue = business.get('total_revenue', 0)
        avg_revenue_per_dn = business.get('avg_revenue_per_dn', 0)
        pending_pod = pod_status.get('pending_pod', 0)
        pending_pgi = delivery_status.get('pending_pgi', 0)
        distance_category = distance.get('distance_category', 'Local')
        
        insights = {}
        
        # TOP STRENGTH
        strengths = []
        if delivery_rate >= 90:
            strengths.append("✅ Excellent Delivery Performance (90%+)")
        if pod_compliance >= 90:
            strengths.append("✅ Excellent POD Compliance (90%+)")
        if avg_revenue_per_dn > 50000:
            strengths.append(f"✅ High Value Orders (PKR {avg_revenue_per_dn:,.0f} per DN)")
        if total_revenue > 1000000:
            strengths.append("✅ Strong Revenue Generation (PKR 1M+)")
        if pending_pgi == 0:
            strengths.append("✅ Zero PGI Pending - Excellent processing")
        if pending_pod <= 5:
            strengths.append("✅ Low POD Pending - Good documentation")
        
        insights['top_strength'] = strengths[0] if strengths else "✅ Consistent Operations"
        
        # BIGGEST RISK
        risks = []
        if delivery_rate < 70:
            risks.append(f"🔴 Low Delivery Rate ({delivery_rate}%) - Affects customer satisfaction")
        if pod_compliance < 70:
            risks.append(f"🔴 Low POD Compliance ({pod_compliance}%) - Affects revenue recognition")
        if pending_pod > 20:
            risks.append(f"🔴 High Pending POD ({pending_pod}) - Documentation backlog")
        if pending_pgi > 10:
            risks.append(f"🔴 High Pending PGI ({pending_pgi}) - Processing delay")
        if len(alerts) > 3:
            risks.append(f"🔴 Multiple Alerts ({len(alerts)}) - Requires immediate attention")
        if distance_category == 'Remote':
            risks.append("🔴 Remote Location - Logistics challenges")
        
        insights['biggest_risk'] = risks[0] if risks else "🟢 No Significant Risks Identified"
        
        # RECOMMENDED ACTION
        if alerts:
            critical_alerts = [a for a in alerts if a.get('severity') == 'critical']
            if critical_alerts:
                insights['recommended_action'] = f"🚨 CRITICAL: {critical_alerts[0].get('recommendation', 'Take immediate action')}"
            else:
                high_alerts = [a for a in alerts if a.get('severity') == 'high']
                if high_alerts:
                    insights['recommended_action'] = high_alerts[0].get('recommendation', 'Review and optimize operations')
                else:
                    insights['recommended_action'] = alerts[0].get('recommendation', 'Review and optimize operations')
        else:
            if delivery_rate < 95:
                insights['recommended_action'] = "Aim for 95%+ delivery rate to achieve A+ grade"
            elif pod_compliance < 95:
                insights['recommended_action'] = "Aim for 95%+ POD compliance to improve revenue recognition"
            else:
                insights['recommended_action'] = "Maintain current performance and explore growth opportunities"
        
        # EXPECTED IMPACT
        health_score = performance.get('health_score', 0)
        risk_level = performance.get('risk_level', 'Unknown')
        
        if health_score >= 75:
            insights['expected_impact'] = "High - Dealer has strong potential for growth"
        elif health_score >= 50:
            if pod_compliance < 70:
                insights['expected_impact'] = "Medium - Improving POD compliance could boost revenue by 15-20%"
            elif delivery_rate < 70:
                insights['expected_impact'] = "Medium - Improving delivery rate could boost revenue by 10-15%"
            else:
                insights['expected_impact'] = "Medium - Optimization opportunities available"
        else:
            if risk_level in ['High', 'Critical']:
                insights['expected_impact'] = "Low - Significant improvements needed to reduce risk"
            else:
                insights['expected_impact'] = "Low - Focus on basic operational improvements first"
        
        return insights


# ==========================================================
# BLOCK 16: DATA QUERY - FIXED (Removed MAX() from metadata)
# ==========================================================

    def _query_all_dealer_data(self, dealer_name: str) -> Dict[str, Any]:
        """
        Query all dealer data from database.
        FIXED: 
        1. Removed MAX() from metadata fields (dealer_code, customer_code, etc.)
        2. Only queries delivery metrics from DeliveryReport
        3. Metadata now comes from DealerMaster table
        """
        try:
            logger.info(f"📊 Querying delivery data for: '{dealer_name}'")
            
            # ==========================================================
            # STEP 1: Query delivery metrics only (no metadata fields)
            # ==========================================================
            result = self.db.query(
                func.min(DeliveryReport.dn_create_date).label("first_dn_date"),
                func.max(DeliveryReport.dn_create_date).label("latest_dn_date"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("total_revenue"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("transit_dns"),
                func.count(distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_dns"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pending_pod_dns"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pending_pgi_dns"),
                func.count(distinct(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no), else_=None))).label("pgi_completed_dns")
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name)
            ).first()
            
            # ==========================================================
            # STEP 2: If no exact match, try ILIKE (partial match)
            # ==========================================================
            if not result or result.total_dns == 0:
                logger.info(f"🔍 No exact match for '{dealer_name}', trying ILIKE...")
                
                ilike_results = self.db.query(
                    func.min(DeliveryReport.dn_create_date).label("first_dn_date"),
                    func.max(DeliveryReport.dn_create_date).label("latest_dn_date"),
                    func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                    func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("total_revenue"),
                    func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                    func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns"),
                    func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("transit_dns"),
                    func.count(distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_dns"),
                    func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pending_pod_dns"),
                    func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pending_pgi_dns"),
                    func.count(distinct(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no), else_=None))).label("pgi_completed_dns")
                ).filter(
                    DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
                ).first()
                
                if ilike_results and ilike_results.total_dns > 0:
                    result = ilike_results
                    logger.info(f"✅ Found data via ILIKE")
                else:
                    # Try token-based matching
                    tokens = dealer_name.split()
                    for token in tokens:
                        if len(token) > 2:
                            token_result = self.db.query(
                                func.min(DeliveryReport.dn_create_date).label("first_dn_date"),
                                func.max(DeliveryReport.dn_create_date).label("latest_dn_date"),
                                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                                func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("total_revenue"),
                                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns"),
                                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("transit_dns"),
                                func.count(distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_dns"),
                                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pending_pod_dns"),
                                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pending_pgi_dns"),
                                func.count(distinct(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no), else_=None))).label("pgi_completed_dns")
                            ).filter(
                                DeliveryReport.customer_name.ilike(f"%{token}%")
                            ).first()
                            
                            if token_result and token_result.total_dns > 0:
                                result = token_result
                                logger.info(f"✅ Found data via token '{token}'")
                                break
            
            if not result or result.total_dns == 0:
                logger.warning(f"❌ No data found for dealer '{dealer_name}'")
                return {}
            
            # ==========================================================
            # STEP 3: Build data dictionary (metadata fields removed)
            # ==========================================================
            logger.info(f"✅ Found {result.total_dns} DNs for dealer")
            
            data = {
                "first_dn_date": result.first_dn_date,
                "latest_dn_date": result.latest_dn_date,
                "total_dns": result.total_dns or 0,
                "total_units": result.total_units or 0,
                "total_revenue": result.total_revenue or 0,
                "delivered_dns": result.delivered_dns or 0,
                "pending_dns": result.pending_dns or 0,
                "transit_dns": result.transit_dns or 0,
                "pod_completed_dns": result.pod_completed_dns or 0,
                "pending_pod_dns": result.pending_pod_dns or 0,
                "pending_pgi_dns": result.pending_pgi_dns or 0,
                "pgi_completed_dns": result.pgi_completed_dns or 0
            }
            
            # ==========================================================
            # Get highest and lowest value DNs
            # ==========================================================
            highest_dn = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_amount
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name),
                DeliveryReport.dn_amount.isnot(None)
            ).order_by(
                desc(DeliveryReport.dn_amount)
            ).first()
            
            if highest_dn:
                data['highest_dn'] = {
                    "dn_no": highest_dn.dn_no,
                    "amount": round(highest_dn.dn_amount or 0, 0)
                }
            else:
                data['highest_dn'] = {"dn_no": "N/A", "amount": 0}
            
            lowest_dn = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_amount
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name),
                DeliveryReport.dn_amount.isnot(None),
                DeliveryReport.dn_amount > 0
            ).order_by(
                DeliveryReport.dn_amount
            ).first()
            
            if lowest_dn:
                data['lowest_dn'] = {
                    "dn_no": lowest_dn.dn_no,
                    "amount": round(lowest_dn.dn_amount or 0, 0)
                }
            else:
                data['lowest_dn'] = {"dn_no": "N/A", "amount": 0}
            
            # ==========================================================
            # Get aging data
            # ==========================================================
            try:
                aging = self.db.query(
                    func.avg(
                        func.extract('day', DeliveryReport.pod_date - DeliveryReport.good_issue_date)
                    ).label('avg_pod_days'),
                    func.avg(
                        func.extract('day', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                    ).label('avg_pgi_days'),
                    func.max(
                        func.extract('day', func.now() - DeliveryReport.good_issue_date)
                    ).label('oldest_pending_pod')
                ).filter(
                    func.lower(DeliveryReport.customer_name) == func.lower(dealer_name),
                    DeliveryReport.dn_create_date.isnot(None)
                ).first()
                
                if aging:
                    data['avg_pod_days'] = round(aging.avg_pod_days or 0, 1)
                    data['avg_pgi_days'] = round(aging.avg_pgi_days or 0, 1)
                    
                    if aging.oldest_pending_pod:
                        days = int(aging.oldest_pending_pod or 0)
                        data['oldest_pending_pod'] = f"{days} days"
                    else:
                        data['oldest_pending_pod'] = 'N/A'
                else:
                    data['avg_pod_days'] = 0
                    data['avg_pgi_days'] = 0
                    data['oldest_pending_pod'] = 'N/A'
                    
            except Exception as e:
                logger.error(f"Aging query error: {e}")
                data['avg_pod_days'] = 0
                data['avg_pgi_days'] = 0
                data['oldest_pending_pod'] = 'N/A'
            
            return data
            
        except Exception as e:
            logger.error(f"Query error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}


# ==========================================================
# BLOCK 17: HELPERS
# ==========================================================

    def _handle_not_found(self, dealer_name: str) -> Dict[str, Any]:
        """Handle dealer not found with suggestions."""
        try:
            similar = self.search.search_dealer(dealer_name, exact=False)
            if similar and len(similar) > 0:
                suggestions = [s['dealer_name'] for s in similar[:5]]
                return {
                    "error": f"Dealer '{dealer_name}' not found",
                    "suggestions": suggestions,
                    "message": f"Did you mean: {', '.join(suggestions[:3])}?"
                }
        except Exception as e:
            logger.error(f"Search error: {e}")
        
        return {
            "error": f"Dealer '{dealer_name}' not found",
            "message": "Please check the spelling or try a shorter version"
        }


# ==========================================================
# BLOCK 18: FACTORY FUNCTION
# ==========================================================

def get_dealer_360_dashboard(db: Session, resolver: EntityResolver, search: SearchEngine) -> Dealer360Dashboard:
    """Factory function to create Dealer360Dashboard instance."""
    return Dealer360Dashboard(db, resolver, search)


# ==========================================================
# BLOCK 19: WHATSAPP FORMATTER (UPDATED)
# ==========================================================
# ==========================================================
# ==========================================================
# BLOCK 19: WHATSAPP FORMATTER (FIXED - NO DEALERMASTER)
# ==========================================================

def format_dealer_360_dashboard(dashboard: Dict[str, Any]) -> str:
    """
    Format 360° dashboard for WhatsApp display.
    FIXED: Works without DealerMaster table.
    """
    if not dashboard:
        return "❌ No data available"
    
    if "error" in dashboard:
        error = dashboard.get("error", "Unknown error")
        suggestions = dashboard.get("suggestions", [])
        if suggestions:
            return f"❌ {error}\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
        return f"❌ {error}"
    
    lines = []
    
    # SECTION 1: DEALER PROFILE
    profile = dashboard.get('profile', {})
    lines.append("🏢 *DEALER 360° DASHBOARD*")
    lines.append("")
    lines.append("📋 *PROFILE*")
    lines.append(f"Dealer: {profile.get('dealer_name', 'N/A')}")
    lines.append(f"Dealer Code: {profile.get('dealer_code', 'N/A')}")
    lines.append(f"Customer Code: {profile.get('customer_code', 'N/A')}")
    lines.append(f"Division: {profile.get('division', 'N/A')}")
    lines.append(f"Sales Office: {profile.get('sales_office', 'N/A')}")
    lines.append(f"Sales Manager: {profile.get('sales_manager', 'N/A')}")
    lines.append(f"Warehouse: {profile.get('warehouse', 'N/A')}")
    lines.append(f"City: {profile.get('city', 'N/A')}")
    lines.append(f"Region: {profile.get('region', 'N/A')}")
    lines.append(f"Active Days: {profile.get('total_active_days', 0)}")
    
    # SECTION 2: BUSINESS VOLUME
    business = dashboard.get('business_volume', {})
    lines.append("")
    lines.append("💰 *BUSINESS VOLUME*")
    lines.append(f"Total DNs: {business.get('total_dns', 0)}")
    lines.append(f"Total Units: {business.get('total_units', 0)}")
    
    revenue = business.get('total_revenue', 0)
    if revenue:
        lines.append(f"Total Revenue: PKR {revenue:,.0f}")
        lines.append(f"Avg per DN: PKR {business.get('avg_revenue_per_dn', 0):,.0f}")
        lines.append(f"Avg per Unit: PKR {business.get('avg_revenue_per_unit', 0):,.0f}")
    else:
        lines.append(f"Total Revenue: PKR 0")
        lines.append(f"Avg per DN: PKR 0")
        lines.append(f"Avg per Unit: PKR 0")
    
    highest = business.get('highest_value_dn', {})
    lowest = business.get('lowest_value_dn', {})
    if highest.get('dn_no', 'N/A') != 'N/A':
        lines.append(f"Highest DN: {highest.get('dn_no', 'N/A')} (PKR {highest.get('amount', 0):,.0f})")
    if lowest.get('dn_no', 'N/A') != 'N/A':
        lines.append(f"Lowest DN: {lowest.get('dn_no', 'N/A')} (PKR {lowest.get('amount', 0):,.0f})")
    
    # SECTION 3: DELIVERY STATUS
    delivery = dashboard.get('delivery_status', {})
    lines.append("")
    lines.append("📦 *DELIVERY STATUS*")
    lines.append(f"✅ Delivered: {delivery.get('delivered', 0)} ({delivery.get('delivered_percent', 0)}%)")
    lines.append(f"🚚 In Transit: {delivery.get('in_transit', 0)} ({delivery.get('in_transit_percent', 0)}%)")
    lines.append(f"⏳ Pending PGI: {delivery.get('pending_pgi', 0)} ({delivery.get('pending_pgi_percent', 0)}%)")
    lines.append(f"📊 Total: {delivery.get('total', 0)}")
    
    # SECTION 4: POD STATUS
    pod = dashboard.get('pod_status', {})
    lines.append("")
    lines.append("📋 *POD STATUS*")
    lines.append(f"POD Completed: {pod.get('pod_completed', 0)}")
    lines.append(f"Pending POD: {pod.get('pending_pod', 0)}")
    lines.append(f"POD Compliance: {pod.get('pod_compliance', 0)}%")
    lines.append(f"Avg POD Days: {pod.get('avg_pod_days', 0)}")
    lines.append(f"Oldest Pending: {pod.get('oldest_pending_pod', 'N/A')}")
    
    # SECTION 5: PGI STATUS
    pgi = dashboard.get('pgi_status', {})
    lines.append("")
    lines.append("🚛 *PGI STATUS*")
    lines.append(f"PGI Completed: {pgi.get('pgi_completed', 0)}")
    lines.append(f"Pending PGI: {pgi.get('pending_pgi', 0)}")
    lines.append(f"PGI Compliance: {pgi.get('pgi_compliance', 0)}%")
    lines.append(f"Avg PGI Days: {pgi.get('avg_pgi_days', 0)}")
    lines.append(f"Oldest Pending: {pgi.get('oldest_pending_pgi', 'N/A')}")
    
    # SECTION 6: PERFORMANCE KPIs
    perf = dashboard.get('performance', {})
    lines.append("")
    lines.append("⚡ *PERFORMANCE*")
    lines.append(f"Delivery Rate: {perf.get('delivery_rate', 0)}%")
    lines.append(f"PGI Rate: {perf.get('pgi_rate', 0)}%")
    lines.append(f"POD Rate: {perf.get('pod_rate', 0)}%")
    lines.append(f"Health Score: {perf.get('health_score', 0)}/100")
    lines.append(f"Risk Level: {perf.get('risk_level', 'Unknown')} ({perf.get('risk_score', 0)}/100)")
    lines.append(f"Performance Grade: {perf.get('performance_grade', 'N/A')}")
    
    # ==========================================================
    # SECTION 7: DISTANCE ANALYTICS (ALWAYS SHOWS)
    # ==========================================================
    distance = dashboard.get('distance', {})
    
    lines.append("")
    lines.append("📍 *DISTANCE*")
    
    warehouse = distance.get('warehouse', 'N/A')
    dealer_city = distance.get('dealer_city', 'N/A')
    
    lines.append(f"Warehouse: {warehouse}")
    lines.append(f"Dealer City: {dealer_city}")
    
    if warehouse != 'N/A' and dealer_city != 'N/A':
        road_distance = distance.get('road_distance')
        if road_distance:
            lines.append(f"Road Distance: {road_distance} km")
            
            driving_time = distance.get('driving_time')
            if driving_time:
                if driving_time < 1:
                    minutes = int(driving_time * 60)
                    lines.append(f"Driving Time: {minutes} minutes")
                else:
                    hours = int(driving_time)
                    minutes = int((driving_time - hours) * 60)
                    if minutes > 0:
                        lines.append(f"Driving Time: {hours}h {minutes}m")
                    else:
                        lines.append(f"Driving Time: {hours} hours")
            
            lines.append(f"Distance Category: {distance.get('distance_category', 'N/A')}")
        else:
            lines.append("📌 Distance: Not calculated")
            lines.append("   💡 Check if distance service is configured")
    else:
        lines.append("📌 Distance: N/A")
        lines.append("   💡 Add warehouse and city to DeliveryReport data")
    
    # SECTION 8: PRODUCT ANALYTICS
    products = dashboard.get('products', {})
    lines.append("")
    lines.append("📦 *PRODUCTS*")
    lines.append(f"Total Products: {products.get('total_products', 0)}")
    lines.append(f"Top Product: {products.get('top_product', 'N/A')}")
    lines.append(f"Highest Revenue: {products.get('highest_revenue_product', 'N/A')}")
    lines.append(f"Highest Volume: {products.get('highest_volume_product', 'N/A')}")
    
    top_5 = products.get('top_5_products', [])
    if top_5:
        lines.append("")
        lines.append("🏆 *Top 5 Products*")
        for i, p in enumerate(top_5[:5], 1):
            revenue = p.get('revenue', 0)
            lines.append(f"{i}. {p.get('product', 'N/A')} (PKR {revenue:,.0f})")
    
    # SECTION 9: CITY ANALYTICS
    cities = dashboard.get('cities', {})
    lines.append("")
    lines.append("🏙️ *CITIES*")
    lines.append(f"Cities Served: {cities.get('cities_served', 0)}")
    lines.append(f"Top City: {cities.get('top_city', 'N/A')}")
    
    revenue_by_city = cities.get('revenue_by_city', {})
    if revenue_by_city:
        lines.append("")
        lines.append("📍 *Revenue By City*")
        for city, revenue in list(revenue_by_city.items())[:3]:
            lines.append(f"• {city}: PKR {revenue:,.0f}")
    
    # SECTION 10: AGING ANALYTICS
    aging = dashboard.get('aging', {})
    lines.append("")
    lines.append("⏳ *AGING*")
    lines.append(f"Avg Delivery Days: {aging.get('avg_delivery_days', 0)}")
    lines.append(f"Avg POD Days: {aging.get('avg_pod_days', 0)}")
    lines.append(f"Avg Cycle Days: {aging.get('avg_cycle_days', 0)}")
    lines.append(f"Max Delivery Days: {aging.get('max_delivery_days', 0)}")
    lines.append(f"Max POD Days: {aging.get('max_pod_days', 0)}")
    
    # SECTION 11: CONTROL TOWER ALERTS
    alerts = dashboard.get('alerts', [])
    if alerts:
        lines.append("")
        lines.append("🚨 *ALERTS*")
        for alert in alerts[:5]:
            severity = alert.get('severity', 'low')
            emoji = "🔴" if severity == 'critical' else "🟠" if severity == 'high' else "🟡"
            lines.append(f"{emoji} {alert.get('message', '')}")
    
    # SECTION 12: EXECUTIVE SUMMARY
    summary = dashboard.get('executive_summary', '')
    if summary:
        lines.append("")
        lines.append("📌 *EXECUTIVE SUMMARY*")
        for line in summary.split('\n'):
            lines.append(line)
    
    # SECTION 13: MANAGEMENT INSIGHTS
    insights = dashboard.get('insights', {})
    if insights:
        lines.append("")
        lines.append("💡 *MANAGEMENT INSIGHTS*")
        lines.append(f"✅ Strength: {insights.get('top_strength', 'N/A')}")
        lines.append(f"⚠️ Risk: {insights.get('biggest_risk', 'N/A')}")
        lines.append(f"🎯 Action: {insights.get('recommended_action', 'N/A')}")
        lines.append(f"📈 Impact: {insights.get('expected_impact', 'N/A')}")
    
    # SECTION 14: WARNINGS
    warning = dashboard.get('_warning')
    if warning:
        lines.append("")
        lines.append(f"⚠️ {warning}")
        suggestion = dashboard.get('_suggestion')
        if suggestion:
            lines.append(f"💡 {suggestion}")
    
    return "\n".join(lines)

# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'Dealer360Dashboard',
    'get_dealer_360_dashboard',
    'format_dealer_360_dashboard'
]

# ==========================================================
# END OF FILE - v3.0 PRODUCTION READY
# ==========================================================
