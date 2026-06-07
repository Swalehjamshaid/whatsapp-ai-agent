# ==========================================================
# FILE: app/services/business_rules_service.py
# ==========================================================
# CENTRAL BUSINESS RULES SERVICE - SINGLE SOURCE OF TRUTH
# ==========================================================

from datetime import date, datetime, timedelta
from typing import Optional, Tuple
from enum import Enum


class DeliveryStatus(str, Enum):
    OPEN = "Open"
    IN_TRANSIT = "In Transit"
    DELIVERED = "Delivered"
    CLOSED = "Closed"


class DelayBucket(str, Enum):
    ON_TIME = "On Time"
    MINOR_DELAY = "Minor Delay"
    MODERATE_DELAY = "Moderate Delay"
    CRITICAL = "Critical"
    SEVERE = "Severe"


class BusinessRulesService:
    """Centralized business rules - single source of truth for all calculations"""
    
    # SLA Configuration
    DISPATCH_SLA_DAYS = 3
    DELIVERY_SLA_DAYS = 1
    POD_SLA_DAYS = 3
    
    # ==================================================
    # AGING CALCULATIONS
    # ==================================================
    
    @staticmethod
    def calculate_dispatch_age(record) -> int:
        """Calculate dispatch age: PGI Date - DN Creation Date"""
        if not record.dn_create_date or not record.good_issue_date:
            return 0
        
        if isinstance(record.good_issue_date, datetime):
            pgi_date = record.good_issue_date.date()
        else:
            pgi_date = record.good_issue_date
        
        if isinstance(record.dn_create_date, datetime):
            create_date = record.dn_create_date.date()
        else:
            create_date = record.dn_create_date
        
        return (pgi_date - create_date).days if pgi_date and create_date else 0
    
    @staticmethod
    def calculate_pending_dispatch_age(record) -> int:
        """Calculate pending dispatch age: Today - DN Creation Date"""
        if not record.dn_create_date:
            return 0
        
        if isinstance(record.dn_create_date, datetime):
            create_date = record.dn_create_date.date()
        else:
            create_date = record.dn_create_date
        
        return (date.today() - create_date).days
    
    @staticmethod
    def calculate_transit_days(record) -> int:
        """Calculate transit days: Delivery Date - PGI Date"""
        if not record.good_issue_date or not record.delivery_date:
            return 0
        
        if isinstance(record.delivery_date, datetime):
            delivery_date = record.delivery_date.date()
        else:
            delivery_date = record.delivery_date
        
        if isinstance(record.good_issue_date, datetime):
            pgi_date = record.good_issue_date.date()
        else:
            pgi_date = record.good_issue_date
        
        return (delivery_date - pgi_date).days if delivery_date and pgi_date else 0
    
    @staticmethod
    def calculate_pod_aging(record) -> int:
        """Calculate POD aging: POD Date - PGI Date"""
        if not record.good_issue_date or not record.pod_date:
            return 0
        
        if isinstance(record.pod_date, datetime):
            pod_date = record.pod_date.date()
        else:
            pod_date = record.pod_date
        
        if isinstance(record.good_issue_date, datetime):
            pgi_date = record.good_issue_date.date()
        else:
            pgi_date = record.good_issue_date
        
        return (pod_date - pgi_date).days if pod_date and pgi_date else 0
    
    @staticmethod
    def calculate_pending_pod_aging(record) -> int:
        """Calculate pending POD aging: Today - PGI Date"""
        if not record.good_issue_date:
            return 0
        
        if isinstance(record.good_issue_date, datetime):
            pgi_date = record.good_issue_date.date()
        else:
            pgi_date = record.good_issue_date
        
        return (date.today() - pgi_date).days
    
    @staticmethod
    def calculate_delivery_cycle(record) -> int:
        """Calculate complete delivery cycle: POD Date - DN Creation Date"""
        if not record.dn_create_date or not record.pod_date:
            return 0
        
        if isinstance(record.pod_date, datetime):
            pod_date = record.pod_date.date()
        else:
            pod_date = record.pod_date
        
        if isinstance(record.dn_create_date, datetime):
            create_date = record.dn_create_date.date()
        else:
            create_date = record.dn_create_date
        
        return (pod_date - create_date).days if pod_date and create_date else 0
    
    # ==================================================
    # STATUS DETERMINATION
    # ==================================================
    
    @staticmethod
    def get_delivery_status(pgi_status: str, pod_status: str) -> DeliveryStatus:
        """Get standardized delivery status"""
        if pgi_status != "Completed":
            return DeliveryStatus.OPEN
        elif pgi_status == "Completed" and pod_status != "Received":
            return DeliveryStatus.IN_TRANSIT
        elif pod_status == "Received":
            return DeliveryStatus.DELIVERED
        return DeliveryStatus.OPEN
    
    @staticmethod
    def is_pgi_completed(status: Optional[str]) -> bool:
        """Check if PGI is completed"""
        if not status:
            return False
        status_lower = status.lower().strip()
        return status_lower in ["completed", "done", "yes", "dispatched"]
    
    @staticmethod
    def is_pod_received(status: Optional[str]) -> bool:
        """Check if POD is received"""
        if not status:
            return False
        status_lower = status.lower().strip()
        return status_lower in ["received", "received ", "pod received", "done", "completed", "yes"]
    
    @staticmethod
    def is_pod_pending(status: Optional[str]) -> bool:
        """Check if POD is pending"""
        return not BusinessRulesService.is_pod_received(status)
    
    # ==================================================
    # SLA CALCULATIONS
    # ==================================================
    
    @classmethod
    def get_dispatch_sla_status(cls, dispatch_age: int) -> Tuple[str, str]:
        """Get dispatch SLA status (status, icon)"""
        if dispatch_age <= cls.DISPATCH_SLA_DAYS:
            return "Within SLA", "✅"
        else:
            return "Breached", "🔴"
    
    @classmethod
    def get_delivery_sla_status(cls, transit_days: int) -> Tuple[str, str]:
        """Get delivery SLA status (status, icon)"""
        if transit_days <= cls.DELIVERY_SLA_DAYS:
            return "Within SLA", "✅"
        else:
            return "Breached", "🔴"
    
    @classmethod
    def get_pod_sla_status(cls, pod_aging: int) -> Tuple[str, str]:
        """Get POD SLA status (status, icon)"""
        if pod_aging <= cls.POD_SLA_DAYS:
            return "Within SLA", "✅"
        else:
            return "Breached", "🔴"
    
    # ==================================================
    # DELAY BUCKET CALCULATIONS
    # ==================================================
    
    @staticmethod
    def get_delay_bucket(days_delayed: int) -> DelayBucket:
        """Get delay bucket classification"""
        if days_delayed <= 1:
            return DelayBucket.ON_TIME
        elif days_delayed <= 3:
            return DelayBucket.MINOR_DELAY
        elif days_delayed <= 7:
            return DelayBucket.MODERATE_DELAY
        elif days_delayed <= 15:
            return DelayBucket.CRITICAL
        else:
            return DelayBucket.SEVERE
    
    @staticmethod
    def get_delay_icon(bucket: DelayBucket) -> str:
        """Get icon for delay bucket"""
        icons = {
            DelayBucket.ON_TIME: "🟢",
            DelayBucket.MINOR_DELAY: "🟡",
            DelayBucket.MODERATE_DELAY: "🟠",
            DelayBucket.CRITICAL: "🔴",
            DelayBucket.SEVERE: "💀"
        }
        return icons.get(bucket, "⚪")
    
    # ==================================================
    # RISK SCORE CALCULATIONS
    # ==================================================
    
    @staticmethod
    def calculate_risk_score(pending_dns: int, total_dns: int, pod_pending: int = 0) -> int:
        """Calculate risk score (0-100)"""
        if total_dns == 0:
            return 0
        
        pending_ratio = (pending_dns / total_dns) * 100
        pod_ratio = (pod_pending / total_dns) * 50 if pod_pending else 0
        
        risk = pending_ratio + pod_ratio
        return min(100, int(risk))
    
    @staticmethod
    def calculate_health_score(pending_dns: int, total_dns: int, pod_pending: int = 0) -> int:
        """Calculate health score (0-100)"""
        risk = BusinessRulesService.calculate_risk_score(pending_dns, total_dns, pod_pending)
        return max(0, 100 - risk)
    
    @staticmethod
    def get_risk_level(risk_score: int) -> Tuple[str, str]:
        """Get risk level and icon"""
        if risk_score >= 70:
            return "Critical", "💀"
        elif risk_score >= 50:
            return "High", "🚨"
        elif risk_score >= 30:
            return "Medium", "⚠️"
        else:
            return "Low", "✅"
