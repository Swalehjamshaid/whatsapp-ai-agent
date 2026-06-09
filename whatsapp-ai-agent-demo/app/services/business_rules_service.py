# ==========================================================
# FILE: app/services/business_rules_service.py (v1.0)
# ==========================================================
# CENTRALIZED BUSINESS RULES
# - Health score calculation
# - Risk score calculation
# - SLA calculations
# - Aging buckets
# - Priority levels
# ==========================================================

from typing import Dict, Any, Optional, Tuple
from datetime import date, datetime
from enum import Enum


class DelayBucket(str, Enum):
    CURRENT = "Current"
    WARNING = "Warning"
    LATE = "Late"
    VERY_LATE = "Very Late"
    CRITICAL = "Critical"


class PriorityLevel(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class BusinessRulesService:
    """
    Centralized business rules for logistics intelligence.
    All scoring and calculation logic lives here.
    """
    
    @staticmethod
    def calculate_health_score(
        delivery_aging: int = 0,
        pending_delivery_aging: int = 0,
        pod_aging: int = 0,
        pending_pod_aging: int = 0
    ) -> int:
        """Calculate health score (0-100). Higher is better."""
        max_aging = max(delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging)
        
        if max_aging <= 0:
            return 100
        elif max_aging <= 1:
            return 95
        elif max_aging <= 3:
            return 80
        elif max_aging <= 7:
            return 60
        elif max_aging <= 15:
            return 40
        elif max_aging <= 30:
            return 25
        else:
            return 10
    
    @staticmethod
    def calculate_risk_score(max_delay: int, total_value: float) -> int:
        """Calculate risk score (0-100). Higher is worse."""
        # Delay component (0-70 points)
        if max_delay <= 1:
            delay_score = 0
        elif max_delay <= 3:
            delay_score = 10
        elif max_delay <= 7:
            delay_score = 25
        elif max_delay <= 15:
            delay_score = 45
        elif max_delay <= 30:
            delay_score = 60
        else:
            delay_score = 70
        
        # Value component (0-30 points)
        if total_value >= 10_000_000:
            value_score = 30
        elif total_value >= 5_000_000:
            value_score = 20
        elif total_value >= 1_000_000:
            value_score = 10
        else:
            value_score = 0
        
        return min(100, delay_score + value_score)
    
    @staticmethod
    def get_risk_level(risk_score: int) -> Tuple[str, str]:
        """Get risk level and icon based on score."""
        if risk_score >= 70:
            return ("Critical", "💀")
        elif risk_score >= 50:
            return ("High", "🚨")
        elif risk_score >= 30:
            return ("Medium", "⚠️")
        else:
            return ("Low", "✅")
    
    @staticmethod
    def get_delay_bucket(max_delay: int) -> DelayBucket:
        """Get delay bucket based on days."""
        if max_delay <= 1:
            return DelayBucket.CURRENT
        elif max_delay <= 3:
            return DelayBucket.WARNING
        elif max_delay <= 7:
            return DelayBucket.LATE
        elif max_delay <= 15:
            return DelayBucket.VERY_LATE
        else:
            return DelayBucket.CRITICAL
    
    @staticmethod
    def get_delay_icon(bucket: DelayBucket) -> str:
        """Get icon for delay bucket."""
        icons = {
            DelayBucket.CURRENT: "✅",
            DelayBucket.WARNING: "⚠️",
            DelayBucket.LATE: "⏰",
            DelayBucket.VERY_LATE: "🔴",
            DelayBucket.CRITICAL: "💀",
        }
        return icons.get(bucket, "❓")
    
    @staticmethod
    def get_priority_level(risk_score: int, max_delay: int, exception_flag: bool) -> PriorityLevel:
        """Get priority level based on multiple factors."""
        if exception_flag and risk_score >= 70:
            return PriorityLevel.CRITICAL
        elif exception_flag and risk_score >= 50:
            return PriorityLevel.HIGH
        elif max_delay > 7:
            return PriorityLevel.MEDIUM
        else:
            return PriorityLevel.LOW
    
    @staticmethod
    def calculate_sla_status(days: int, sla_limit: int = 3) -> Tuple[str, str]:
        """Calculate SLA status and icon."""
        if days <= sla_limit:
            return ("On Time", "✅")
        else:
            return ("Delayed", "🔴")
    
    @staticmethod
    def calculate_aging_days(date_field) -> int:
        """Calculate aging days from a date field."""
        if not date_field:
            return 0
        
        if isinstance(date_field, datetime):
            date_field = date_field.date()
        elif isinstance(date_field, str):
            try:
                date_field = datetime.strptime(date_field, "%Y-%m-%d").date()
            except:
                return 0
        
        return max(0, (date.today() - date_field).days)


# Singleton instance
business_rules = BusinessRulesService()
