# ==========================================================
# FILE: app/services/logistics_query_service.py (ENTERPRISE v3.0)
# ==========================================================
# LOGISTICS QUERY SERVICE
# - DN Intelligence (Complete 360 view)
# - Pending DNs, Delayed DNs, POD/PGI status
# - Dealer, City, Warehouse intelligence
# - Exception management (critical DNs, high-value pending)
# ==========================================================

import time
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import date, datetime, timedelta
from enum import Enum
from dataclasses import dataclass

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, desc
from loguru import logger

from app.models import DeliveryReport
from app.services.business_rules_service import BusinessRulesService


class DNStage(str, Enum):
    """DN lifecycle stages"""
    DN_CREATED = "DN Created"
    AWAITING_PGI = "Awaiting PGI"
    PGI_COMPLETED = "PGI Completed"
    IN_TRANSIT = "In Transit"
    DELIVERED = "Delivered"
    POD_PENDING = "POD Pending"
    CLOSED = "Closed"


class DNRiskLevel(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    MINIMAL = "Minimal"


@dataclass
class DNRiskAssessment:
    level: DNRiskLevel
    score: int
    reasons: List[str]
    icon: str
    action_required: str


class LogisticsQueryService:
    """
    Enterprise Logistics Query Service
    Handles all DN-related database queries and intelligence
    """
    
    def __init__(self, db: Session, cache_service=None):
        self.db = db
        self.cache = cache_service
        self.business_rules = BusinessRulesService()
        
        self.query_stats = {
            "total_queries": 0,
            "avg_response_time_ms": 0,
            "cache_hits": 0,
            "errors": 0
        }
        
        logger.info("✅ Logistics Query Service v3.0 initialized")
    
    # ==========================================================
    # DN 360 INTELLIGENCE (Multi-Product Aggregation)
    # ==========================================================
    
    def get_complete_dn_intelligence(self, dn_no: str) -> Dict[str, Any]:
        """
        Get complete intelligence for a DN.
        Aggregates ALL products in the DN (not just first row).
        """
        start_time = time.time()
        
        if not dn_no:
            return {"error": "Please provide a DN number"}
        
        logger.info(f"🔍 DN Search: {dn_no}")
        
        # Check cache
        cache_key = f"dn_intelligence:{dn_no}"
        if self.cache and self.cache.enabled:
            cached = self.cache.get(cache_key)
            if cached:
                self.query_stats["cache_hits"] += 1
                logger.info(f"✅ DN {dn_no} returned from cache")
                return cached
        
        try:
            # Get ALL records for this DN
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_no
            ).all()
            
            logger.info(f"📊 DN {dn_no} - Records Found: {len(records)}")
            
            if not records:
                return {"error": f"DN {dn_no} not found"}
            
            first_record = records[0]
            
            # Aggregate ALL products
            products = []
            total_qty = 0
            total_value = 0
            divisions = set()
            
            for record in records:
                qty = float(record.dn_qty or 0)
                value = float(record.dn_amount or 0)
                total_qty += qty
                total_value += value
                
                product_code = getattr(record, 'material_no', None) or getattr(record, 'product', 'Unknown')
                division = self._get_division(product_code)
                divisions.add(division)
                
                products.append({
                    "product": product_code,
                    "qty": qty,
                    "value": value,
                    "division": division,
                    "percentage_of_dn": (value / total_value * 100) if total_value > 0 else 0
                })
            
            # Calculate aging metrics
            delivery_aging = self.business_rules.calculate_delivery_aging(first_record)
            pending_delivery_aging = self.business_rules.calculate_pending_delivery_aging(first_record)
            pod_aging = self.business_rules.calculate_pod_aging(first_record)
            pending_pod_aging = self.business_rules.calculate_pending_pod_aging(first_record)
            
            # Determine DN stage
            stage = self._determine_dn_stage(first_record)
            stage_icon = self._get_stage_icon(stage)
            
            # Calculate SLA status
            delivery_sla = self.business_rules.check_delivery_sla(delivery_aging)
            pod_sla = self.business_rules.check_pod_sla(pod_aging)
            
            # Calculate delay bucket
            max_delay = max(delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging)
            delay_bucket = self.business_rules.get_delay_bucket(max_delay)
            
            # Get delivery status
            status = self.business_rules.get_delivery_status(
                getattr(first_record, 'pgi_status', 'Pending'),
                getattr(first_record, 'pod_status', 'Pending')
            )
            
            # Calculate health score
            health_score = self.business_rules.calculate_health_score(
                delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging
            )
            
            # Calculate risk assessment
            risk_score = self.business_rules.calculate_risk_score(max_delay, total_value)
            
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
            
            # Get timeline
            timeline = self._get_timeline(first_record)
            
            # Get recommendations
            recommendations = self._get_recommendations(first_record, delay_bucket, pending_pod_aging, total_value)
            
            result = {
                "success": True,
                "dn_no": dn_no,
                "dealer": first_record.customer_name or "Unknown",
                "city": first_record.ship_to_city or "Unknown",
                "warehouse": first_record.warehouse or "Unknown",
                "stage": stage.value,
                "stage_icon": stage_icon,
                "status": status.value,
                "status_icon": self.business_rules.get_status_icon(status),
                "products": products,
                "total_qty": total_qty,
                "total_value": total_value,
                "product_count": len(products),
                "division_count": len(divisions),
                "pgi_date": first_record.good_issue_date,
                "delivery_date": first_record.delivery_date,
                "pod_date": first_record.pod_date,
                "aging": {
                    "delivery_aging": delivery_aging,
                    "pending_delivery_aging": pending_delivery_aging,
                    "pod_aging": pod_aging,
                    "pending_pod_aging": pending_pod_aging
                },
                "sla": {
                    "delivery_status": delivery_sla.status,
                    "delivery_icon": delivery_sla.icon,
                    "pod_status": pod_sla.status,
                    "pod_icon": pod_sla.icon
                },
                "delay_bucket": delay_bucket.value,
                "delay_icon": self.business_rules.get_delay_icon(delay_bucket),
                "health_score": health_score,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "risk_icon": risk_icon,
                "timeline": timeline,
                "recommendations": recommendations
            }
            
            # Cache for 5 minutes
            if self.cache and self.cache.enabled:
                self.cache.set(cache_key, result, ttl=300)
            
            elapsed_ms = (time.time() - start_time) * 1000
            self.query_stats["total_queries"] += 1
            self.query_stats["avg_response_time_ms"] = (
                (self.query_stats["avg_response_time_ms"] * (self.query_stats["total_queries"] - 1) + elapsed_ms) 
                / self.query_stats["total_queries"]
            )
            logger.info(f"⚡ DN {dn_no} - Response Time: {elapsed_ms:.2f}ms")
            
            return result
            
        except Exception as e:
            logger.exception(f"DN Intelligence Failed for {dn_no}: {e}")
            self.query_stats["errors"] += 1
            return {"error": str(e), "dn_no": dn_no}
    
    # ==========================================================
    # PENDING & DELAYED DNS
    # ==========================================================
    
    def get_pending_dns(self, limit: int = 20) -> List[Dict]:
        """Get pending DNs (PGI not completed)"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.pgi_status != "Completed"
            ).order_by(DeliveryReport.dn_create_date).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "created_date": r.dn_create_date,
                "aging_days": self.business_rules.calculate_pending_delivery_aging(r)
            } for r in records]
        except Exception as e:
            logger.error(f"Error getting pending DNs: {e}")
            return []
    
    def get_delayed_dns(self, days_threshold: int = 7, limit: int = 20) -> List[Dict]:
        """Get delayed DNs"""
        try:
            threshold_date = date.today() - timedelta(days=days_threshold)
            
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_create_date <= threshold_date
            ).order_by(DeliveryReport.dn_create_date).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "delay_days": self.business_rules.calculate_pending_delivery_aging(r)
            } for r in records]
        except Exception as e:
            logger.error(f"Error getting delayed DNs: {e}")
            return []
    
    def get_pending_pods(self, limit: int = 20) -> List[Dict]:
        """Get DNs with pending POD"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).order_by(DeliveryReport.delivery_date.desc()).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "delivery_date": r.delivery_date,
                "pending_days": self.business_rules.calculate_pending_pod_aging(r)
            } for r in records]
        except Exception as e:
            logger.error(f"Error getting pending PODs: {e}")
            return []
    
    def get_pending_pgi(self, limit: int = 20) -> List[Dict]:
        """Get DNs with pending PGI"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.pgi_status != "Completed"
            ).order_by(DeliveryReport.dn_create_date).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "created_date": r.dn_create_date,
                "pending_days": self.business_rules.calculate_pending_delivery_aging(r)
            } for r in records]
        except Exception as e:
            logger.error(f"Error getting pending PGI: {e}")
            return []
    
    # ==========================================================
    # EXCEPTION MANAGEMENT (Critical DNs, High Value)
    # ==========================================================
    
    def get_critical_dns(self, limit: int = 20) -> List[Dict]:
        """Get critically delayed DNs (>15 days)"""
        try:
            threshold_date = date.today() - timedelta(days=15)
            
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_create_date <= threshold_date
            ).order_by(DeliveryReport.dn_create_date).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "aging_days": self.business_rules.calculate_pending_delivery_aging(r),
                "severity": "CRITICAL"
            } for r in records]
        except Exception as e:
            logger.error(f"Error getting critical DNs: {e}")
            return []
    
    def get_high_value_pending_dns(self, threshold: float = 1_000_000, limit: int = 20) -> List[Dict]:
        """Get high-value pending DNs"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_amount >= threshold
            ).order_by(desc(DeliveryReport.dn_amount)).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "created_date": r.dn_create_date,
                "aging_days": self.business_rules.calculate_pending_delivery_aging(r)
            } for r in records]
        except Exception as e:
            logger.error(f"Error getting high value pending DNs: {e}")
            return []
    
    # ==========================================================
    # DEALER INTELLIGENCE
    # ==========================================================
    
    def get_dealer_pending_dns(self, dealer_name: str, limit: int = 20) -> List[Dict]:
        """Get pending DNs for a specific dealer"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.pgi_status != "Completed"
            ).order_by(DeliveryReport.dn_create_date).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "value": float(r.dn_amount or 0),
                "created_date": r.dn_create_date,
                "aging_days": self.business_rules.calculate_pending_delivery_aging(r)
            } for r in records]
        except Exception as e:
            logger.error(f"Error getting dealer pending DNs: {e}")
            return []
    
    def get_dealer_pending_pods(self, dealer_name: str, limit: int = 20) -> List[Dict]:
        """Get pending PODs for a specific dealer"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name == dealer_name,
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).order_by(desc(DeliveryReport.delivery_date)).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "value": float(r.dn_amount or 0),
                "delivery_date": r.delivery_date,
                "pending_days": self.business_rules.calculate_pending_pod_aging(r)
            } for r in records]
        except Exception as e:
            logger.error(f"Error getting dealer pending PODs: {e}")
            return []
    
    def get_dealer_delivery_performance(self, dealer_name: str) -> Dict:
        """Get delivery performance for a dealer"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name == dealer_name
            ).all()
            
            if not records:
                return {"error": f"No records found for dealer '{dealer_name}'"}
            
            total_dns = len(set(r.dn_no for r in records))
            completed = len([r for r in records if r.pgi_status == "Completed"])
            pod_received = len([r for r in records if r.pod_status == "Received"])
            
            return {
                "dealer": dealer_name,
                "total_dns": total_dns,
                "completed_dns": completed,
                "completion_rate": (completed / total_dns * 100) if total_dns else 0,
                "pod_compliance": (pod_received / total_dns * 100) if total_dns else 0
            }
        except Exception as e:
            logger.error(f"Error getting dealer performance: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # PRIVATE METHODS
    # ==========================================================
    
    def _determine_dn_stage(self, record) -> DNStage:
        """Determine the current stage of a DN"""
        if getattr(record, 'pod_status', '') == "Received":
            return DNStage.CLOSED
        elif getattr(record, 'pod_status', '') == "Pending" and getattr(record, 'delivery_date', None):
            return DNStage.POD_PENDING
        elif getattr(record, 'delivery_date', None):
            return DNStage.DELIVERED
        elif getattr(record, 'good_issue_date', None):
            return DNStage.IN_TRANSIT
        elif getattr(record, 'pgi_status', '') == "Completed":
            return DNStage.PGI_COMPLETED
        elif getattr(record, 'dn_create_date', None):
            return DNStage.AWAITING_PGI
        else:
            return DNStage.DN_CREATED
    
    def _get_stage_icon(self, stage: DNStage) -> str:
        icons = {
            DNStage.DN_CREATED: "📄",
            DNStage.AWAITING_PGI: "⏳",
            DNStage.PGI_COMPLETED: "✅",
            DNStage.IN_TRANSIT: "🚚",
            DNStage.DELIVERED: "📦",
            DNStage.POD_PENDING: "📋",
            DNStage.CLOSED: "🔒"
        }
        return icons.get(stage, "❓")
    
    def _get_division(self, product: str) -> str:
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
        return "Other"
    
    def _get_timeline(self, record) -> Dict:
        events = []
        
        if record.dn_create_date:
            events.append({"stage": "DN Created", "date": record.dn_create_date, "icon": "📄"})
        if record.good_issue_date:
            events.append({"stage": "PGI Completed", "date": record.good_issue_date, "icon": "🚚"})
        if record.delivery_date:
            events.append({"stage": "Delivered", "date": record.delivery_date, "icon": "✅"})
        if record.pod_date:
            events.append({"stage": "POD Received", "date": record.pod_date, "icon": "📋"})
        
        return {"events": events, "total_events": len(events)}
    
    def _get_recommendations(self, record, delay_bucket, pending_pod_aging, total_value) -> List[str]:
        recommendations = []
        
        if getattr(record, 'pgi_status', '') != "Completed":
            recommendations.append("⚠️ Pending PGI - Coordinate with warehouse for dispatch")
        
        if getattr(record, 'pgi_status', '') == "Completed" and getattr(record, 'pod_status', '') != "Received":
            if pending_pod_aging > 14:
                recommendations.append(f"📋 CRITICAL: POD pending for {pending_pod_aging} days")
            elif pending_pod_aging > 7:
                recommendations.append(f"📋 URGENT: POD pending for {pending_pod_aging} days")
            else:
                recommendations.append("📋 POD pending - Send reminder")
        
        if delay_bucket and delay_bucket.value in ["Severe", "Critical"]:
            recommendations.append("🚨 Escalate to regional manager")
        
        if total_value > 5_000_000:
            recommendations.append("💰 High value DN - Prioritize resolution")
        
        if not recommendations:
            recommendations.append("✅ DN is on track - No action required")
        
        return recommendations
    
    def get_query_stats(self) -> Dict:
        return {
            "total_queries": self.query_stats["total_queries"],
            "avg_response_time_ms": round(self.query_stats["avg_response_time_ms"], 2),
            "cache_hits": self.query_stats["cache_hits"],
            "errors": self.query_stats["errors"]
        }


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_logistics_query_service(db: Session, cache_service=None) -> LogisticsQueryService:
    return LogisticsQueryService(db, cache_service)
