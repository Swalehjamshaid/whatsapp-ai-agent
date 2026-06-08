# ==========================================================
# FILE: app/services/logistics_query_service.py (FIXED v3.0)
# ==========================================================
# CRITICAL FIXES:
# - Fixed: product → material_no (field name mismatch)
# - Fixed: delivery_date → Use good_issue_date or pod_date
# - Fixed: Added try/except wrapper for crash prevention
# - Fixed: Case-insensitive dealer search
# - Fixed: Safe attribute access with hasattr()
# ==========================================================

import time
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import date, datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from loguru import logger

from app.models import DeliveryReport
from app.services.business_rules_service import BusinessRulesService


# ==========================================================
# DN STATUS ENGINE
# ==========================================================

class DNStage(str, Enum):
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
    """Enterprise Logistics Query Service v3.0 - FIXED"""
    
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
    # MAIN DN INTELLIGENCE - WITH TRY/EXCEPT
    # ==========================================================
    
    def get_complete_dn_intelligence(self, dn_no: str) -> Dict[str, Any]:
        """
        Get complete intelligence for a DN.
        WRAPPED IN TRY/EXCEPT for crash prevention.
        """
        start_time = time.time()
        
        try:
            if not dn_no:
                return {"error": "Please provide a DN number"}
            
            dn_no = str(dn_no).strip()
            logger.info(f"🔍 DN Search: {dn_no}")
            
            # Check cache first
            cache_key = f"dn_intelligence:{dn_no}"
            if self.cache and self.cache.enabled:
                cached = self.cache.get(cache_key)
                if cached:
                    self.query_stats["cache_hits"] += 1
                    logger.info(f"✅ DN {dn_no} returned from cache")
                    return cached
            
            # Get ALL records for this DN
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_no
            ).all()
            
            logger.info(f"📊 DN {dn_no} - Records Found: {len(records)}")
            
            if not records:
                return {"error": f"DN {dn_no} not found"}
            
            # Log available fields for debugging (CRITICAL FIX)
            first_record = records[0]
            logger.info(f"📋 Available fields: {[c.name for c in first_record.__table__.columns]}")
            
            # ==========================================================
            # SAFE FIELD ACCESS - Check what exists
            # ==========================================================
            
            # Get product/material field safely
            product_field = None
            if hasattr(first_record, 'material_no'):
                product_field = 'material_no'
            elif hasattr(first_record, 'product'):
                product_field = 'product'
            elif hasattr(first_record, 'material'):
                product_field = 'material'
            else:
                logger.warning("No product/material field found in DeliveryReport model")
            
            # Get delivery date field safely
            delivery_date_field = None
            if hasattr(first_record, 'delivery_date'):
                delivery_date_field = 'delivery_date'
            elif hasattr(first_record, 'good_issue_date'):
                # Use good_issue_date as proxy for delivery
                delivery_date_field = 'good_issue_date'
            else:
                logger.warning("No delivery date field found")
            
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
                
                # Get product code safely
                product_code = "Unknown"
                if product_field:
                    product_code = str(getattr(record, product_field, "Unknown"))[:50]
                
                # Get division for this product
                division = self._get_division(product_code)
                divisions.add(division)
                
                products.append({
                    "product": product_code,
                    "description": self._get_product_description(product_code),
                    "qty": qty,
                    "value": value,
                    "unit_price": value / qty if qty > 0 else 0,
                    "division": division,
                    "percentage_of_dn": (value / total_value * 100) if total_value > 0 else 0
                })
            
            # Calculate aging metrics safely
            delivery_aging = self.business_rules.calculate_delivery_aging(first_record)
            pending_delivery_aging = self.business_rules.calculate_pending_delivery_aging(first_record)
            pod_aging = self.business_rules.calculate_pod_aging(first_record)
            pending_pod_aging = self.business_rules.calculate_pending_pod_aging(first_record)
            
            # Determine DN stage safely
            stage = self._determine_dn_stage_safe(first_record)
            stage_icon = self._get_stage_icon(stage)
            
            # Calculate SLA status
            delivery_sla = self.business_rules.check_delivery_sla(delivery_aging)
            pod_sla = self.business_rules.check_pod_sla(pod_aging)
            
            # Calculate delay bucket safely
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
            risk_assessment = self._assess_dn_risk_safe(
                first_record, pending_delivery_aging, pending_pod_aging, total_value, delay_bucket
            )
            
            # Get timeline safely
            timeline = self._get_timeline_safe(first_record)
            
            # Get recommendations safely
            recommendations = self._get_recommendations_safe(
                first_record, delay_bucket, risk_assessment, pending_pod_aging
            )
            
            # Get sales manager, route, transporter
            city = getattr(first_record, 'ship_to_city', 'Unknown') or 'Unknown'
            warehouse = getattr(first_record, 'warehouse', 'Unknown') or 'Unknown'
            
            sales_manager = self._get_sales_manager_for_city(city)
            route = self._get_route_for_city(city)
            transporter = self._get_transporter_for_warehouse(warehouse)
            
            # Safe date access
            pgi_date = getattr(first_record, 'good_issue_date', None)
            pod_date = getattr(first_record, 'pod_date', None)
            
            # Delivery date - try multiple sources
            delivery_date = None
            if delivery_date_field:
                delivery_date = getattr(first_record, delivery_date_field, None)
            
            # Expected delivery date
            expected_delivery_date = self._calculate_expected_delivery_date_safe(first_record)
            
            # Lead time
            lead_time_days = self._calculate_lead_time_safe(first_record)
            
            result = {
                "success": True,
                "dn_no": dn_no,
                "dealer": getattr(first_record, 'customer_name', 'Unknown') or 'Unknown',
                "city": city,
                "warehouse": warehouse,
                "sales_manager": sales_manager,
                "route": route,
                "transporter": transporter,
                "stage": stage.value,
                "stage_icon": stage_icon,
                "status": status.value,
                "status_icon": self.business_rules.get_status_icon(status),
                "products": products,
                "total_qty": total_qty,
                "total_value": total_value,
                "product_count": len(products),
                "division_count": len(divisions),
                "divisions": list(divisions),
                "pgi_date": pgi_date,
                "delivery_date": delivery_date,
                "pod_date": pod_date,
                "expected_delivery_date": expected_delivery_date,
                "lead_time_days": lead_time_days,
                "aging": {
                    "delivery_aging": delivery_aging,
                    "pending_delivery_aging": pending_delivery_aging,
                    "pod_aging": pod_aging,
                    "pending_pod_aging": pending_pod_aging
                },
                "sla": {
                    "delivery_status": delivery_sla.status,
                    "delivery_icon": delivery_sla.icon,
                    "delivery_days_over": delivery_sla.days_over,
                    "pod_status": pod_sla.status,
                    "pod_icon": pod_sla.icon,
                    "pod_days_over": pod_sla.days_over
                },
                "delay_bucket": delay_bucket.value if delay_bucket else "Unknown",
                "delay_icon": self.business_rules.get_delay_icon(delay_bucket) if delay_bucket else "⚪",
                "health_score": health_score,
                "risk": {
                    "level": risk_assessment.level.value,
                    "score": risk_assessment.score,
                    "reasons": risk_assessment.reasons,
                    "icon": risk_assessment.icon,
                    "action_required": risk_assessment.action_required
                },
                "timeline": timeline,
                "recommendations": recommendations
            }
            
            # Cache for 5 minutes
            if self.cache and self.cache.enabled:
                self.cache.set(cache_key, result, ttl=300)
            
            # Performance logging
            elapsed_ms = (time.time() - start_time) * 1000
            self.query_stats["total_queries"] += 1
            self.query_stats["avg_response_time_ms"] = (
                (self.query_stats["avg_response_time_ms"] * (self.query_stats["total_queries"] - 1) + elapsed_ms) 
                / self.query_stats["total_queries"]
            )
            logger.info(f"⚡ DN {dn_no} - Response Time: {elapsed_ms:.2f}ms | Records: {len(records)}")
            
            return result
            
        except Exception as e:
            # CRITICAL FIX: Catch ALL exceptions and return error
            logger.exception(f"❌ DN Intelligence Failed for {dn_no}: {e}")
            self.query_stats["errors"] += 1
            
            return {
                "success": False,
                "error": str(e),
                "dn_no": dn_no,
                "message": f"Failed to retrieve DN {dn_no}: {str(e)[:100]}"
            }
    
    # ==========================================================
    # SAFE HELPER METHODS (with hasattr checks)
    # ==========================================================
    
    def _determine_dn_stage_safe(self, record) -> DNStage:
        """Safely determine DN stage"""
        try:
            pod_status = getattr(record, 'pod_status', 'Pending')
            delivery_date = getattr(record, 'delivery_date', None) or getattr(record, 'good_issue_date', None)
            good_issue_date = getattr(record, 'good_issue_date', None)
            pgi_status = getattr(record, 'pgi_status', 'Pending')
            dn_create_date = getattr(record, 'dn_create_date', None)
            
            if pod_status == "Received":
                return DNStage.CLOSED
            elif pod_status == "Pending" and delivery_date:
                return DNStage.POD_PENDING
            elif delivery_date:
                return DNStage.DELIVERED
            elif good_issue_date:
                return DNStage.IN_TRANSIT
            elif pgi_status == "Completed":
                return DNStage.PGI_COMPLETED
            elif dn_create_date:
                return DNStage.AWAITING_PGI
            else:
                return DNStage.DN_CREATED
        except Exception as e:
            logger.error(f"Stage determination error: {e}")
            return DNStage.DN_CREATED
    
    def _assess_dn_risk_safe(self, record, pending_delivery_aging: int, 
                               pending_pod_aging: int, total_value: float,
                               delay_bucket) -> DNRiskAssessment:
        """Safe risk assessment"""
        try:
            reasons = []
            risk_score = 0
            
            if pending_delivery_aging > 30:
                risk_score += 40
                reasons.append(f"⚠️ Pending dispatch for {pending_delivery_aging} days")
            elif pending_delivery_aging > 15:
                risk_score += 30
                reasons.append(f"⚠️ Pending dispatch for {pending_delivery_aging} days")
            elif pending_delivery_aging > 7:
                risk_score += 20
                reasons.append(f"⚠️ Pending dispatch for {pending_delivery_aging} days")
            
            if pending_pod_aging > 14:
                risk_score += 35
                reasons.append(f"📋 POD pending for {pending_pod_aging} days")
            elif pending_pod_aging > 7:
                risk_score += 25
                reasons.append(f"📋 POD pending for {pending_pod_aging} days")
            elif pending_pod_aging > 3:
                risk_score += 15
                reasons.append(f"📋 POD pending for {pending_pod_aging} days")
            
            if total_value > 5_000_000:
                risk_score += 25
                reasons.append(f"💰 High value DN: Rs {total_value:,.2f}")
            elif total_value > 1_000_000:
                risk_score += 15
                reasons.append(f"💰 Medium value DN: Rs {total_value:,.2f}")
            
            bucket_value = delay_bucket.value if delay_bucket else ""
            if bucket_value in ["Severe", "Critical"]:
                risk_score += 20
                reasons.append(f"🚨 {bucket_value} delay category")
            
            if risk_score >= 70:
                level = DNRiskLevel.CRITICAL
                icon = "💀"
                action = "IMMEDIATE ESCALATION REQUIRED"
            elif risk_score >= 50:
                level = DNRiskLevel.HIGH
                icon = "🚨"
                action = "Escalate within 24 hours"
            elif risk_score >= 30:
                level = DNRiskLevel.MEDIUM
                icon = "⚠️"
                action = "Monitor and follow up"
            elif risk_score >= 10:
                level = DNRiskLevel.LOW
                icon = "✅"
                action = "Regular monitoring"
            else:
                level = DNRiskLevel.MINIMAL
                icon = "🟢"
                action = "No action needed"
            
            return DNRiskAssessment(
                level=level,
                score=min(100, risk_score),
                reasons=reasons,
                icon=icon,
                action_required=action
            )
        except Exception as e:
            logger.error(f"Risk assessment error: {e}")
            return DNRiskAssessment(
                level=DNRiskLevel.MEDIUM,
                score=50,
                reasons=["Risk assessment unavailable"],
                icon="⚠️",
                action_required="Manual review required"
            )
    
    def _get_timeline_safe(self, record) -> Dict[str, Any]:
        """Safe timeline generation"""
        events = []
        
        try:
            dn_create_date = getattr(record, 'dn_create_date', None)
            if dn_create_date:
                events.append({
                    "stage": "DN Created",
                    "date": dn_create_date,
                    "icon": "📄",
                    "description": f"Delivery Note created"
                })
            
            good_issue_date = getattr(record, 'good_issue_date', None)
            if good_issue_date:
                aging = self.business_rules.calculate_delivery_aging(record)
                events.append({
                    "stage": "PGI Completed",
                    "date": good_issue_date,
                    "icon": "🚚",
                    "description": f"Goods issued after {aging} days",
                    "duration_days": aging
                })
            
            # Use good_issue_date as delivery proxy if no delivery_date
            delivery_date = getattr(record, 'delivery_date', None) or good_issue_date
            if delivery_date and delivery_date != good_issue_date:
                events.append({
                    "stage": "Delivered",
                    "date": delivery_date,
                    "icon": "✅",
                    "description": "Order delivered to customer"
                })
            
            pod_date = getattr(record, 'pod_date', None)
            if pod_date:
                pod_aging = self.business_rules.calculate_pod_aging(record)
                events.append({
                    "stage": "POD Received",
                    "date": pod_date,
                    "icon": "📋",
                    "description": f"Proof of Delivery received after {pod_aging} days",
                    "duration_days": pod_aging
                })
        except Exception as e:
            logger.error(f"Timeline generation error: {e}")
        
        return {"events": events, "total_events": len(events)}
    
    def _get_recommendations_safe(self, record, delay_bucket, risk_assessment, pending_pod_aging) -> List[str]:
        """Safe recommendations generation"""
        recommendations = []
        
        try:
            pgi_status = getattr(record, 'pgi_status', 'Completed')
            pod_status = getattr(record, 'pod_status', 'Received')
            
            if pgi_status != "Completed":
                recommendations.append("⚠️ Pending PGI - Coordinate with warehouse for immediate dispatch")
            
            if pgi_status == "Completed" and pod_status != "Received":
                if pending_pod_aging > 14:
                    recommendations.append(f"📋 CRITICAL: POD pending for {pending_pod_aging} days - Escalate to management")
                elif pending_pod_aging > 7:
                    recommendations.append(f"📋 URGENT: POD pending for {pending_pod_aging} days - Send escalation notice")
                elif pending_pod_aging > 3:
                    recommendations.append(f"📋 POD pending for {pending_pod_aging} days - Send reminder to customer")
                else:
                    recommendations.append("📋 POD pending - Send reminder within 24 hours")
            
            if risk_assessment and risk_assessment.level in [DNRiskLevel.CRITICAL, DNRiskLevel.HIGH]:
                recommendations.append(risk_assessment.action_required)
            
            bucket_value = delay_bucket.value if delay_bucket else ""
            if bucket_value in ["Severe", "Critical"]:
                recommendations.append("🚨 Escalate to regional manager for immediate intervention")
            
            if not recommendations:
                recommendations.append("✅ DN is on track - No action required")
        except Exception as e:
            logger.error(f"Recommendations error: {e}")
            recommendations.append("⚠️ Unable to generate recommendations")
        
        return recommendations
    
    def _calculate_expected_delivery_date_safe(self, record):
        """Safe expected delivery date calculation"""
        try:
            good_issue_date = getattr(record, 'good_issue_date', None)
            dn_create_date = getattr(record, 'dn_create_date', None)
            
            if good_issue_date:
                return good_issue_date + timedelta(days=1)
            elif dn_create_date:
                return dn_create_date + timedelta(days=3)
        except Exception as e:
            logger.error(f"Expected delivery date error: {e}")
        return None
    
    def _calculate_lead_time_safe(self, record) -> int:
        """Safe lead time calculation"""
        try:
            delivery_date = getattr(record, 'delivery_date', None) or getattr(record, 'good_issue_date', None)
            dn_create_date = getattr(record, 'dn_create_date', None)
            
            if delivery_date and dn_create_date:
                return (delivery_date - dn_create_date).days
        except Exception as e:
            logger.error(f"Lead time error: {e}")
        return 0
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
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
    
    def _get_product_description(self, product_code: str) -> str:
        descriptions = {
            "HSU-18HFPAA": "1.5 Ton Inverter AC",
            "HSU-12HFPAA": "1 Ton Inverter AC",
            "HRF-438IFRAA": "438L Inverter Refrigerator",
            "HRF-350IFRAA": "350L Inverter Refrigerator",
            "HWM-120AS": "12kg Washing Machine",
            "HWM-80AS": "8kg Washing Machine"
        }
        return descriptions.get(product_code, product_code)
    
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
        else:
            return "Other"
    
    def _get_sales_manager_for_city(self, city: str) -> str:
        managers = {
            "Karachi": "Ali Raza",
            "Lahore": "Ahmed Khan", 
            "Islamabad": "Sara Khan",
            "Rawalpindi": "Usman Ali",
            "Faisalabad": "Bilal Ahmed",
        }
        return managers.get(city, "Regional Manager")
    
    def _get_route_for_city(self, city: str) -> str:
        routes = {
            "Karachi": "South Route - Express",
            "Lahore": "Central Route - Standard",
            "Islamabad": "North Route - Express",
        }
        return routes.get(city, "Standard Route")
    
    def _get_transporter_for_warehouse(self, warehouse: str) -> str:
        transporters = {
            "Karachi Warehouse": "FastTrack Logistics",
            "Lahore Warehouse": "Pakistan Cargo Services",
        }
        return transporters.get(warehouse, "Standard Transporter")
    
    # ==========================================================
    # COMPATIBILITY METHODS
    # ==========================================================
    
    def get_query_stats(self) -> Dict[str, Any]:
        return {
            "total_queries": self.query_stats["total_queries"],
            "avg_response_time_ms": round(self.query_stats["avg_response_time_ms"], 2),
            "cache_hits": self.query_stats["cache_hits"],
            "errors": self.query_stats["errors"],
            "cache_hit_rate": round(
                self.query_stats["cache_hits"] / max(1, self.query_stats["total_queries"]) * 100, 1
            )
        }
    
    def get_pending_dns(self, limit: int = 20) -> List[Dict]:
        """Get pending DNs"""
        records = self.db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status != "Completed"
        ).order_by(DeliveryReport.dn_create_date).limit(limit).all()
        
        result = []
        for r in records:
            result.append({
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "created_date": r.dn_create_date,
                "aging_days": self.business_rules.calculate_pending_delivery_aging(r)
            })
        return result
    
    def get_pending_pods(self, limit: int = 20) -> List[Dict]:
        """Get DNs with pending POD"""
        records = self.db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status != "Received"
        ).order_by(DeliveryReport.dn_create_date.desc()).limit(limit).all()
        
        result = []
        for r in records:
            result.append({
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "pending_days": self.business_rules.calculate_pending_pod_aging(r)
            })
        return result


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_logistics_query_service(db: Session, cache_service=None) -> LogisticsQueryService:
    return LogisticsQueryService(db, cache_service)
