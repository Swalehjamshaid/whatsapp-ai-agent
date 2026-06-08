# ==========================================================
# FILE: app/services/business_rules_service.py
# ==========================================================

from enum import Enum
from typing import Tuple, Optional
from datetime import datetime, date
from dataclasses import dataclass


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


class SLABucket(str, Enum):
    ON_TIME = "On Time"
    DELAYED = "Delayed"


@dataclass
class SLAResult:
    status: SLABucket
    icon: str
    days_over: int


class BusinessRulesService:
    """
    Centralized business rules engine.
    ALL calculations should go through this service.
    """
    
    # Configuration constants
    DELIVERY_SLA_DAYS = 1
    POD_SLA_DAYS = 3
    HEALTH_SCORE_MAX = 100
    HEALTH_SCORE_MIN = 0
    RISK_WEIGHT_VALUE = 0.4
    RISK_WEIGHT_AGING = 0.6
    
    # ==========================================================
    # AGING CALCULATIONS
    # ==========================================================
    
    @staticmethod
    def calculate_delivery_aging(record) -> int:
        """
        Delivery Aging = PGI Date - DN Creation Date
        Rule 2 from requirements
        """
        pgi_date = BusinessRulesService._get_date(record, 'good_issue_date')
        create_date = BusinessRulesService._get_date(record, 'dn_create_date')
        
        if not pgi_date or not create_date:
            return 0
        
        return (pgi_date - create_date).days
    
    @staticmethod
    def calculate_pending_delivery_aging(record) -> int:
        """
        Pending Delivery Aging = Today - DN Creation Date
        Rule 3 from requirements
        """
        create_date = BusinessRulesService._get_date(record, 'dn_create_date')
        
        if not create_date:
            return 0
        
        return (date.today() - create_date).days
    
    @staticmethod
    def calculate_pod_aging(record) -> int:
        """
        POD Aging = POD Date - PGI Date
        Rule 1 from requirements
        """
        if BusinessRulesService._get_status(record, 'pod_status') != "Received":
            return 0
        
        pod_date = BusinessRulesService._get_date(record, 'pod_date')
        pgi_date = BusinessRulesService._get_date(record, 'good_issue_date')
        
        if not pod_date or not pgi_date:
            return 0
        
        return (pod_date - pgi_date).days
    
    @staticmethod
    def calculate_pending_pod_aging(record) -> int:
        """
        Pending POD Aging = Today - PGI Date
        Rule 4 from requirements
        """
        if BusinessRulesService._get_status(record, 'pod_status') == "Received":
            return 0
        
        pgi_date = BusinessRulesService._get_date(record, 'good_issue_date')
        
        if not pgi_date:
            return 0
        
        return (date.today() - pgi_date).days
    
    @staticmethod
    def calculate_pgi_aging(record) -> int:
        """
        PGI Aging = Today - DN Creation Date (if PGI not completed)
        """
        if BusinessRulesService._get_status(record, 'pgi_status') == "Completed":
            return 0
        
        create_date = BusinessRulesService._get_date(record, 'dn_create_date')
        
        if not create_date:
            return 0
        
        return (date.today() - create_date).days
    
    # ==========================================================
    # SLA CALCULATIONS
    # ==========================================================
    
    @classmethod
    def check_delivery_sla(cls, delivery_aging: int) -> SLAResult:
        """Check if delivery meets SLA"""
        if delivery_aging <= cls.DELIVERY_SLA_DAYS:
            return SLAResult(
                status=SLABucket.ON_TIME,
                icon="✅",
                days_over=0
            )
        else:
            return SLAResult(
                status=SLABucket.DELAYED,
                icon="🔴",
                days_over=delivery_aging - cls.DELIVERY_SLA_DAYS
            )
    
    @classmethod
    def check_pod_sla(cls, pod_aging: int) -> SLAResult:
        """Check if POD meets SLA"""
        if pod_aging <= cls.POD_SLA_DAYS:
            return SLAResult(
                status=SLABucket.ON_TIME,
                icon="✅",
                days_over=0
            )
        else:
            return SLAResult(
                status=SLABucket.DELAYED,
                icon="🔴",
                days_over=pod_aging - cls.POD_SLA_DAYS
            )
    
    # ==========================================================
    # DELAY BUCKET CALCULATIONS
    # ==========================================================
    
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
    
    # ==========================================================
    # STATUS CALCULATIONS
    # ==========================================================
    
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
    def get_status_icon(status: DeliveryStatus) -> str:
        """Get icon for delivery status"""
        icons = {
            DeliveryStatus.OPEN: "📝",
            DeliveryStatus.IN_TRANSIT: "🚚",
            DeliveryStatus.DELIVERED: "✅",
            DeliveryStatus.CLOSED: "🔒"
        }
        return icons.get(status, "❓")
    
    # ==========================================================
    # SCORE CALCULATIONS
    # ==========================================================
    
    @classmethod
    def calculate_health_score(
        cls,
        delivery_aging: int,
        pending_delivery_aging: int,
        pod_aging: int,
        pending_pod_aging: int
    ) -> int:
        """Calculate DN health score (0-100)"""
        max_delay = max(delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging)
        
        if max_delay <= 1:
            return 100
        elif max_delay <= 3:
            return 85
        elif max_delay <= 7:
            return 70
        elif max_delay <= 15:
            return 50
        elif max_delay <= 30:
            return 30
        else:
            return max(0, 100 - (max_delay * 2))
    
    @classmethod
    def calculate_risk_score(cls, delay_days: int, amount: float) -> int:
        """
        Calculate risk score (0-100)
        Higher score = higher risk
        """
        # Delay contribution (0-60 points)
        if delay_days <= 1:
            delay_score = 0
        elif delay_days <= 3:
            delay_score = 20
        elif delay_days <= 7:
            delay_score = 40
        elif delay_days <= 15:
            delay_score = 50
        else:
            delay_score = min(60, 40 + delay_days // 5)
        
        # Value contribution (0-40 points)
        if amount <= 10000:
            value_score = 0
        elif amount <= 50000:
            value_score = 10
        elif amount <= 200000:
            value_score = 20
        elif amount <= 500000:
            value_score = 30
        else:
            value_score = 40
        
        return min(100, delay_score + value_score)
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    @staticmethod
    def _get_date(record, field: str) -> Optional[date]:
        """Safely extract date from record"""
        value = getattr(record, field, None)
        if not value:
            return None
        if isinstance(value, datetime):
            return value.date()
        return value
    
    @staticmethod
    def _get_status(record, field: str) -> str:
        """Safely extract status from record"""
        return getattr(record, field, "") or ""
