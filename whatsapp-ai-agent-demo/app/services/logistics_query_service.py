# ==========================================================
# FILE: app/services/logistics_query_service.py
# ==========================================================

from typing import Dict, Any, Optional, List
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from loguru import logger

from app.models import DeliveryReport
from app.services.business_rules_service import BusinessRulesService


class LogisticsQueryService:
    """
    Logistics query service for DN-related operations.
    Fast, cached, no AI.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.business_rules = BusinessRulesService()
    
    def get_complete_dn_intelligence(self, dn_no: str) -> Dict[str, Any]:
        """
        Get complete intelligence for a DN.
        This is the most important function - answers 80% of queries.
        """
        if not dn_no:
            return {"error": "Please provide a DN number"}
        
        # Get DN record
        record = self.db.query(DeliveryReport).filter(
            DeliveryReport.dn_no == dn_no
        ).first()
        
        if not record:
            return {"error": f"DN {dn_no} not found"}
        
        # Calculate all metrics using business rules
        delivery_aging = self.business_rules.calculate_delivery_aging(record)
        pending_delivery_aging = self.business_rules.calculate_pending_delivery_aging(record)
        pod_aging = self.business_rules.calculate_pod_aging(record)
        pending_pod_aging = self.business_rules.calculate_pending_pod_aging(record)
        
        delivery_sla = self.business_rules.check_delivery_sla(delivery_aging)
        pod_sla = self.business_rules.check_pod_sla(pod_aging)
        
        delay_bucket = self.business_rules.get_delay_bucket(
            max(delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging)
        )
        
        status = self.business_rules.get_delivery_status(record.pgi_status, record.pod_status)
        health_score = self.business_rules.calculate_health_score(
            delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging
        )
        risk_score = self.business_rules.calculate_risk_score(
            delivery_aging, pending_delivery_aging, record.dn_amount
        )
        
        # Get products in DN
        products = self._get_dn_products(dn_no)
        
        # Build response
        return {
            "dn_no": dn_no,
            "dealer": record.customer_name or "Unknown",
            "city": record.ship_to_city or "Unknown",
            "warehouse": record.warehouse or "Unknown",
            "division": self._get_division(record.product),
            "status": status.value,
            "status_icon": self.business_rules.get_status_icon(status),
            "delivery_aging": delivery_aging,
            "pending_delivery_aging": pending_delivery_aging,
            "pod_aging": pod_aging,
            "pending_pod_aging": pending_pod_aging,
            "delivery_sla": delivery_sla,
            "pod_sla": pod_sla,
            "delay_bucket": delay_bucket.value,
            "delay_icon": self.business_rules.get_delay_icon(delay_bucket),
            "health_score": health_score,
            "risk_score": risk_score,
            "risk_level": self._get_risk_level(risk_score),
            "total_value": float(record.dn_amount or 0),
            "total_units": float(record.dn_qty or 0),
            "products": products,
            "timeline": self._get_timeline(record),
            "recommendations": self._get_recommendations(record, delay_bucket, risk_score)
        }
    
    def get_pending_dns(self, limit: int = 20) -> List[Dict]:
        """Get pending DNs (PGI not completed)"""
        records = self.db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status != "Completed"
        ).order_by(DeliveryReport.dn_create_date).limit(limit).all()
        
        return [self._to_pending_dn_dict(r) for r in records]
    
    def get_delayed_dns(self, days_threshold: int = 3, limit: int = 20) -> List[Dict]:
        """Get delayed DNs"""
        threshold_date = date.today() - timedelta(days=days_threshold)
        
        records = self.db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status != "Completed",
            DeliveryReport.dn_create_date <= threshold_date
        ).order_by(DeliveryReport.dn_create_date).limit(limit).all()
        
        return [self._to_delayed_dn_dict(r) for r in records]
    
    def get_pending_pods(self, limit: int = 20) -> List[Dict]:
        """Get DNs with pending POD"""
        records = self.db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending"
        ).order_by(DeliveryReport.delivery_date.desc()).limit(limit).all()
        
        return [self._to_pending_pod_dict(r) for r in records]
    
    def get_pending_pgi(self, limit: int = 20) -> List[Dict]:
        """Get DNs with pending PGI"""
        records = self.db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status != "Completed"
        ).order_by(DeliveryReport.dn_create_date).limit(limit).all()
        
        return [self._to_pending_pgi_dict(r) for r in records]
    
    def get_dn_timeline(self, dn_no: str) -> Dict[str, Any]:
        """Get timeline for a DN"""
        record = self.db.query(DeliveryReport).filter(
            DeliveryReport.dn_no == dn_no
        ).first()
        
        if not record:
            return {"error": f"DN {dn_no} not found"}
        
        return self._get_timeline(record)
    
    def get_dn_products(self, dn_no: str) -> List[Dict]:
        """Get products in a DN"""
        return self._get_dn_products(dn_no)
    
    def get_oldest_pending_dn(self) -> Optional[Dict]:
        """Get oldest pending DN"""
        record = self.db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status != "Completed"
        ).order_by(DeliveryReport.dn_create_date).first()
        
        if record:
            return self._to_pending_dn_dict(record)
        return None
    
    # ==========================================================
    # PRIVATE METHODS
    # ==========================================================
    
    def _get_dn_products(self, dn_no: str) -> List[Dict]:
        """Get products for a DN"""
        records = self.db.query(DeliveryReport).filter(
            DeliveryReport.dn_no == dn_no
        ).all()
        
        products = []
        for r in records:
            products.append({
                "product": r.product,
                "qty": float(r.dn_qty or 0),
                "value": float(r.dn_amount or 0)
            })
        return products
    
    def _get_timeline(self, record) -> Dict[str, Any]:
        """Get DN timeline events"""
        events = []
        
        if record.dn_create_date:
            events.append({
                "stage": "DN Created",
                "date": record.dn_create_date,
                "icon": "📄"
            })
        
        if record.good_issue_date:
            aging = self.business_rules.calculate_delivery_aging(record)
            events.append({
                "stage": "PGI Completed",
                "date": record.good_issue_date,
                "icon": "🚚",
                "aging_days": aging
            })
        
        if record.delivery_date:
            events.append({
                "stage": "Delivered",
                "date": record.delivery_date,
                "icon": "✅"
            })
        
        if record.pod_date:
            pod_aging = self.business_rules.calculate_pod_aging(record)
            events.append({
                "stage": "POD Received",
                "date": record.pod_date,
                "icon": "📋",
                "aging_days": pod_aging
            })
        
        return {"events": events, "total_events": len(events)}
    
    def _get_division(self, product: str) -> str:
        """Get division from product code"""
        if not product:
            return "Unknown"
        
        product_upper = product.upper()
        if product_upper.startswith(('AC', 'HSU', 'HSP', 'HSW')):
            return "Air Conditioners"
        elif product_upper.startswith(('REF', 'HRF', 'HVF')):
            return "Refrigerators"
        elif product_upper.startswith(('TV', 'LED', 'LCD')):
            return "Televisions"
        elif product_upper.startswith(('WM', 'HWM')):
            return "Washing Machines"
        else:
            return "Other"
    
    def _get_risk_level(self, risk_score: int) -> str:
        """Get risk level from score"""
        if risk_score >= 70:
            return "Critical"
        elif risk_score >= 50:
            return "High"
        elif risk_score >= 30:
            return "Medium"
        elif risk_score >= 10:
            return "Low"
        else:
            return "Minimal"
    
    def _get_recommendations(self, record, delay_bucket, risk_score) -> List[str]:
        """Get recommendations based on DN status"""
        recommendations = []
        
        if record.pgi_status != "Completed":
            recommendations.append("⚠️ Pending PGI - Coordinate with warehouse for dispatch")
        
        if record.pgi_status == "Completed" and record.pod_status != "Received":
            aging = self.business_rules.calculate_pending_pod_aging(record)
            if aging > 7:
                recommendations.append(f"📋 POD pending for {aging} days - Urgent follow-up required")
            elif aging > 3:
                recommendations.append(f"📋 POD pending for {aging} days - Send reminder to customer")
        
        if delay_bucket.value in ["Critical", "Severe"]:
            recommendations.append("🚨 Escalate to regional manager for immediate action")
        
        if risk_score > 50:
            recommendations.append("💰 High revenue at risk - Prioritize resolution")
        
        if not recommendations:
            recommendations.append("✅ DN is on track - No action required")
        
        return recommendations
    
    def _to_pending_dn_dict(self, record) -> Dict:
        return {
            "dn_no": record.dn_no,
            "dealer": record.customer_name,
            "value": float(record.dn_amount or 0),
            "created_date": record.dn_create_date,
            "aging_days": self.business_rules.calculate_pending_delivery_aging(record)
        }
    
    def _to_delayed_dn_dict(self, record) -> Dict:
        return {
            "dn_no": record.dn_no,
            "dealer": record.customer_name,
            "value": float(record.dn_amount or 0),
            "delay_days": self.business_rules.calculate_pending_delivery_aging(record)
        }
    
    def _to_pending_pod_dict(self, record) -> Dict:
        return {
            "dn_no": record.dn_no,
            "dealer": record.customer_name,
            "value": float(record.dn_amount or 0),
            "delivery_date": record.delivery_date,
            "pending_days": self.business_rules.calculate_pending_pod_aging(record)
        }
    
    def _to_pending_pgi_dict(self, record) -> Dict:
        return {
            "dn_no": record.dn_no,
            "dealer": record.customer_name,
            "value": float(record.dn_amount or 0),
            "created_date": record.dn_create_date,
            "pending_days": self.business_rules.calculate_pending_delivery_aging(record)
        }
