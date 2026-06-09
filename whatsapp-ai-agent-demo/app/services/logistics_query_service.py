# ==========================================================
# FILE: app/services/logistics_query_service.py (ENTERPRISE v4.1)
# ==========================================================
# LOGISTICS QUERY SERVICE - FULLY IMPROVED with ALL FIXES
# 
# CRITICAL FIXES APPLIED:
# ✅ Fix #1: DN Number Format Normalization (strip, clean, handle spaces, .0 suffix)
# ✅ Fix #2: Flexible Column Mapping (supports multiple column name variations)
# ✅ Fix #3: Flexible PGI Status Mapping (supports "Completed", "PGI Done", "Done", "Y", "1")
# ✅ Fix #4: Flexible POD Status Mapping (supports "Received", "POD Done", "Completed")
# ✅ Fix #5: Business Rules Service Defensive Programming (safe defaults for all methods)
# ✅ Fix #6: Fixed product_code variable bug (now uses first record, not last)
# ✅ Fix #7: Added detailed debug logging for troubleshooting
# ✅ Fix #8: Enhanced DN lookup with multiple search strategies
# ==========================================================

import time
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import date, datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, desc
from loguru import logger

from app.models import DeliveryReport


class DNStage(str, Enum):
    """DN lifecycle stages"""
    DN_CREATED = "DN Created"
    AWAITING_PGI = "Awaiting PGI"
    PGI_COMPLETED = "PGI Completed"
    IN_TRANSIT = "In Transit"
    DELIVERED = "Delivered"
    POD_PENDING = "POD Pending"
    CLOSED = "Closed"
    UNKNOWN = "Unknown"


class DNRiskLevel(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    MINIMAL = "Minimal"


class SLABucket(str, Enum):
    ON_TIME = "On Time"
    BREACHED = "Breached"


class PriorityLevel(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class DelayBucket(str, Enum):
    CURRENT = "Current"
    WARNING = "Warning"
    LATE = "Late"
    VERY_LATE = "Very Late"
    CRITICAL = "Critical"
    SEVERE = "Severe"
    UNKNOWN = "Unknown"


@dataclass
class DNRiskAssessment:
    level: DNRiskLevel
    score: int
    reasons: List[str]
    icon: str
    action_required: str


class LogisticsQueryService:
    """
    Enterprise Logistics Query Service v4.1
    Handles all DN-related database queries with robust error handling
    """
    
    # ==========================================================
    # FIX #2: FLEXIBLE COLUMN MAPPING
    # ==========================================================
    COLUMN_MAPPINGS = {
        "customer_name": ["customer_name", "dealer_name", "dealer", "customer", "buyer_name", "customerName"],
        "ship_to_city": ["ship_to_city", "city", "destination_city", "shipping_city", "delivery_city"],
        "warehouse": ["warehouse", "warehouse_name", "storage_location", "plant", "supplying_plant"],
        "pgi_status": ["pgi_status", "PGI_Status", "pgiStatus", "goods_issue_status", "dispatch_status"],
        "pod_status": ["pod_status", "POD_Status", "podStatus", "proof_of_delivery_status"],
        "material_no": ["material_no", "material_number", "product_code", "product", "item_code"],
        "dn_amount": ["dn_amount", "amount", "value", "total_amount", "invoice_value"],
        "dn_qty": ["dn_qty", "quantity", "qty", "order_qty", "delivered_qty"],
        "dn_create_date": ["dn_create_date", "created_date", "order_date", "document_date", "creation_date"],
        "good_issue_date": ["good_issue_date", "goods_issue_date", "dispatch_date", "shipping_date", "pgi_date"],
        "pod_date": ["pod_date", "pod_received_date", "delivery_date", "proof_date", "received_date"],
    }
    
    # ==========================================================
    # FIX #3: FLEXIBLE PGI STATUS MAPPING
    # ==========================================================
    PGI_COMPLETED_VALUES = {
        "completed", "pgi completed", "pgi_completed", "done", "yes", "y", "1", "true", 
        "finished", "complete", "dispatched", "shipped", "goods issued", "goods_issued"
    }
    
    PGI_PENDING_VALUES = {
        "pending", "not completed", "not_completed", "no", "n", "0", "false", 
        "waiting", "open", "incomplete", "not dispatched", "not_shipped"
    }
    
    # ==========================================================
    # FIX #4: FLEXIBLE POD STATUS MAPPING
    # ==========================================================
    POD_RECEIVED_VALUES = {
        "received", "pod received", "pod_received", "done", "yes", "y", "1", "true",
        "completed", "complete", "signed", "delivered", "confirmed"
    }
    
    POD_PENDING_VALUES = {
        "pending", "not received", "not_received", "no", "n", "0", "false",
        "waiting", "open", "incomplete", "not signed", "missing"
    }
    
    # ==========================================================
    # INITIALIZATION
    # ==========================================================
    
    def __init__(self, db: Session, cache_service=None):
        self.db = db
        self.cache = cache_service
        
        # Cache TTL by type
        self.cache_ttl = {
            "dn_intelligence": 60,      # 1 minute for DN lookups
            "dealer_dashboard": 300,    # 5 minutes
            "kpi_dashboard": 300,       # 5 minutes
            "analytics": 600            # 10 minutes
        }
        
        self.query_stats = {
            "total_queries": 0,
            "avg_response_time_ms": 0,
            "cache_hits": 0,
            "errors": 0,
            "dn_not_found": 0
        }
        
        # Audit log
        self.audit_log = []
        
        # Debug log for troubleshooting
        self.debug_enabled = True
        
        logger.info("=" * 60)
        logger.info("✅ Logistics Query Service v4.1 initialized")
        logger.info("   - Flexible column mapping enabled")
        logger.info("   - Flexible PGI/POD status mapping enabled")
        logger.info("   - Multiple DN search strategies enabled")
        logger.info("=" * 60)
    
    # ==========================================================
    # FIX #1: DN NUMBER NORMALIZATION
    # ==========================================================
    
    def _normalize_dn_number(self, dn_no: str) -> List[str]:
        """
        Normalize DN number and generate multiple search variations.
        
        Handles:
        - Whitespace trimming
        - "DN " prefix removal
        - .0 suffix removal (from Excel)
        - Leading/trailing zeros
        """
        if not dn_no:
            return []
        
        dn_no = str(dn_no).strip()
        variations = [dn_no]
        
        # Remove "DN " prefix if present
        if dn_no.upper().startswith("DN "):
            variations.append(dn_no[3:].strip())
        
        # Remove .0 suffix (Excel auto-format)
        if dn_no.endswith(".0"):
            variations.append(dn_no[:-2].strip())
        
        # Remove any non-numeric characters (keep digits only)
        digits_only = re.sub(r'\D', '', dn_no)
        if digits_only and digits_only != dn_no:
            variations.append(digits_only)
        
        # Remove leading zeros
        stripped_zeros = digits_only.lstrip('0') if digits_only else dn_no
        if stripped_zeros and stripped_zeros != dn_no:
            variations.append(stripped_zeros)
        
        # Remove trailing zeros (for codes)
        stripped_trailing = dn_no.rstrip('0')
        if stripped_trailing and stripped_trailing != dn_no:
            variations.append(stripped_trailing)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_variations = []
        for v in variations:
            if v not in seen:
                seen.add(v)
                unique_variations.append(v)
        
        logger.debug(f"DN Normalization: '{dn_no}' -> {unique_variations}")
        return unique_variations
    
    def _find_dn_records(self, dn_no: str) -> List:
        """
        Find DN records using multiple search strategies.
        
        Strategy 1: Exact match on original
        Strategy 2: Exact match on variations
        Strategy 3: LIKE search with wildcards
        Strategy 4: Numeric match (if applicable)
        """
        variations = self._normalize_dn_number(dn_no)
        
        # Strategy 1 & 2: Exact matches
        for variant in variations:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == variant
            ).all()
            
            if records:
                logger.info(f"✅ Found {len(records)} records with exact match: '{variant}'")
                return records
        
        # Strategy 3: LIKE search (case-insensitive)
        for variant in variations:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no.like(f"%{variant}%")
            ).all()
            
            if records:
                logger.info(f"✅ Found {len(records)} records with LIKE match: '%{variant}%'")
                return records
        
        # Strategy 4: If digits only, try numeric search
        digits = re.sub(r'\D', '', dn_no)
        if digits:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no.like(f"%{digits}%")
            ).all()
            if records:
                logger.info(f"✅ Found {len(records)} records with numeric match: '{digits}'")
                return records
        
        return []
    
    # ==========================================================
    # FIX #2: FLEXIBLE COLUMN GETTER
    # ==========================================================
    
    def _get_field_value(self, record, logical_name: str, default: Any = None) -> Any:
        """
        Get field value using flexible column mapping.
        
        Tries multiple possible column names for each logical field.
        """
        if not record:
            return default
        
        possible_columns = self.COLUMN_MAPPINGS.get(logical_name, [logical_name])
        
        for col in possible_columns:
            try:
                # Try direct attribute access
                value = getattr(record, col, None)
                if value is not None and value != "":
                    return value
                
                # Try dictionary-style access (for dict-like objects)
                if hasattr(record, '__getitem__'):
                    try:
                        value = record[col]
                        if value is not None and value != "":
                            return value
                    except (KeyError, TypeError, IndexError):
                        pass
            except Exception:
                continue
        
        # Log missing field for debugging
        if self.debug_enabled:
            logger.debug(f"Field '{logical_name}' not found in record, using default: {default}")
        
        return default
    
    # ==========================================================
    # FIX #3 & #4: FLEXIBLE STATUS CHECKERS
    # ==========================================================
    
    def _is_pgi_completed(self, record) -> bool:
        """Check if PGI is completed using flexible status mapping"""
        pgi_status = self._get_field_value(record, "pgi_status", "")
        
        if not pgi_status:
            return False
        
        pgi_status_lower = str(pgi_status).lower().strip()
        
        # Check for completed values
        for completed_val in self.PGI_COMPLETED_VALUES:
            if completed_val in pgi_status_lower or pgi_status_lower == completed_val:
                logger.debug(f"PGI status '{pgi_status}' recognized as COMPLETED")
                return True
        
        # Check for pending values
        for pending_val in self.PGI_PENDING_VALUES:
            if pending_val in pgi_status_lower or pgi_status_lower == pending_val:
                logger.debug(f"PGI status '{pgi_status}' recognized as PENDING")
                return False
        
        # Default: treat as not completed
        logger.warning(f"Unknown PGI status value: '{pgi_status}', treating as PENDING")
        return False
    
    def _is_pod_received(self, record) -> bool:
        """Check if POD is received using flexible status mapping"""
        pod_status = self._get_field_value(record, "pod_status", "")
        
        if not pod_status:
            return False
        
        pod_status_lower = str(pod_status).lower().strip()
        
        # Check for received values
        for received_val in self.POD_RECEIVED_VALUES:
            if received_val in pod_status_lower or pod_status_lower == received_val:
                logger.debug(f"POD status '{pod_status}' recognized as RECEIVED")
                return True
        
        # Check for pending values
        for pending_val in self.POD_PENDING_VALUES:
            if pending_val in pod_status_lower or pod_status_lower == pending_val:
                logger.debug(f"POD status '{pod_status}' recognized as PENDING")
                return False
        
        # Default: treat as not received
        logger.warning(f"Unknown POD status value: '{pod_status}', treating as PENDING")
        return False
    
    # ==========================================================
    # FIX #5: BUSINESS RULES (Defensive Programming)
    # ==========================================================
    
    def _calculate_delivery_aging(self, record) -> int:
        """Calculate delivery aging in days with safe defaults"""
        try:
            good_issue_date = self._get_field_value(record, "good_issue_date")
            if good_issue_date:
                if isinstance(good_issue_date, datetime):
                    good_issue_date = good_issue_date.date()
                elif isinstance(good_issue_date, str):
                    good_issue_date = datetime.strptime(good_issue_date, "%Y-%m-%d").date()
                
                delta = (date.today() - good_issue_date).days
                return max(0, delta)
        except Exception as e:
            logger.warning(f"Error calculating delivery aging: {e}")
        return 0
    
    def _calculate_pending_delivery_aging(self, record) -> int:
        """Calculate pending delivery aging in days"""
        try:
            dn_create_date = self._get_field_value(record, "dn_create_date")
            if dn_create_date:
                if isinstance(dn_create_date, datetime):
                    dn_create_date = dn_create_date.date()
                elif isinstance(dn_create_date, str):
                    dn_create_date = datetime.strptime(dn_create_date, "%Y-%m-%d").date()
                
                delta = (date.today() - dn_create_date).days
                return max(0, delta)
        except Exception as e:
            logger.warning(f"Error calculating pending delivery aging: {e}")
        return 0
    
    def _calculate_pod_aging(self, record) -> int:
        """Calculate POD aging in days"""
        try:
            pod_date = self._get_field_value(record, "pod_date")
            if pod_date:
                if isinstance(pod_date, datetime):
                    pod_date = pod_date.date()
                elif isinstance(pod_date, str):
                    pod_date = datetime.strptime(pod_date, "%Y-%m-%d").date()
                
                delta = (date.today() - pod_date).days
                return max(0, delta)
        except Exception as e:
            logger.warning(f"Error calculating POD aging: {e}")
        return 0
    
    def _calculate_pending_pod_aging(self, record) -> int:
        """Calculate pending POD aging in days"""
        try:
            good_issue_date = self._get_field_value(record, "good_issue_date")
            if good_issue_date:
                if isinstance(good_issue_date, datetime):
                    good_issue_date = good_issue_date.date()
                elif isinstance(good_issue_date, str):
                    good_issue_date = datetime.strptime(good_issue_date, "%Y-%m-%d").date()
                
                delta = (date.today() - good_issue_date).days
                return max(0, delta)
        except Exception as e:
            logger.warning(f"Error calculating pending POD aging: {e}")
        return 0
    
    def _calculate_health_score(self, delivery_aging: int, pending_delivery_aging: int,
                                  pod_aging: int, pending_pod_aging: int) -> int:
        """Calculate health score (0-100) with safe defaults"""
        try:
            # Lower is better for all metrics
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
        except Exception as e:
            logger.warning(f"Error calculating health score: {e}")
            return 50  # Neutral default
    
    def _calculate_risk_score(self, max_delay: int, total_value: float) -> int:
        """Calculate risk score (0-100)"""
        try:
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
        except Exception as e:
            logger.warning(f"Error calculating risk score: {e}")
            return 0
    
    def _get_delay_bucket(self, max_delay: int) -> DelayBucket:
        """Get delay bucket based on days"""
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
    
    def _get_delay_icon(self, bucket: DelayBucket) -> str:
        """Get icon for delay bucket"""
        icons = {
            DelayBucket.CURRENT: "✅",
            DelayBucket.WARNING: "⚠️",
            DelayBucket.LATE: "⏰",
            DelayBucket.VERY_LATE: "🔴",
            DelayBucket.CRITICAL: "💀",
            DelayBucket.SEVERE: "💀",
            DelayBucket.UNKNOWN: "❓"
        }
        return icons.get(bucket, "❓")
    
    def _get_status_icon(self, status) -> str:
        """Get icon for status"""
        if not status:
            return "❓"
        status_lower = str(status).lower()
        if "delivered" in status_lower or "completed" in status_lower:
            return "✅"
        elif "pending" in status_lower:
            return "⏳"
        elif "delay" in status_lower:
            return "⚠️"
        elif "critical" in status_lower:
            return "💀"
        return "📦"
    
    def _get_delivery_status(self, pgi_completed: bool, pod_received: bool) -> str:
        """Get delivery status string"""
        if pgi_completed and pod_received:
            return "Delivered"
        elif pgi_completed and not pod_received:
            return "Delivered - POD Pending"
        elif not pgi_completed:
            return "Pending Dispatch"
        return "In Transit"
    
    # ==========================================================
    # FIX #6: FIXED PRODUCT CODE BUG
    # ==========================================================
    
    def _get_product_code(self, record) -> str:
        """Safely get product code from record"""
        # Try multiple possible column names
        product_code = self._get_field_value(record, "material_no")
        
        if product_code:
            return str(product_code)
        
        # Try alternative: product from any field containing 'product'
        for col in dir(record):
            if 'product' in col.lower() or 'material' in col.lower():
                try:
                    val = getattr(record, col)
                    if val and str(val).strip():
                        return str(val)
                except Exception:
                    pass
        
        return "Unknown"
    
    def _get_division(self, product: str) -> str:
        """Get division based on product code"""
        if not product or product == "Unknown":
            return "Unknown"
        
        product_upper = product.upper()
        
        if product_upper.startswith(('AC', 'HSU', 'HSP', 'HSW', 'AIR')):
            return "Air Conditioners"
        elif product_upper.startswith(('REF', 'HRF', 'HVF', 'FRIDGE')):
            return "Refrigerators"
        elif product_upper.startswith(('TV', 'LED', 'LCD', 'OLED')):
            return "Televisions"
        elif product_upper.startswith(('WM', 'HWM', 'WASH')):
            return "Washing Machines"
        elif product_upper.startswith(('MIC', 'MW', 'OVEN')):
            return "Microwaves"
        
        return "Other"
    
    # ==========================================================
    # DN STAGE DETERMINATION (FIXED)
    # ==========================================================
    
    def _determine_dn_stage(self, pgi_completed: bool, pod_received: bool,
                              good_issue_date: Any, dn_create_date: Any) -> DNStage:
        """Determine DN stage based on actual data"""
        if pod_received:
            return DNStage.CLOSED
        elif pgi_completed and not pod_received:
            return DNStage.POD_PENDING
        elif good_issue_date:
            return DNStage.DELIVERED
        elif pgi_completed:
            return DNStage.PGI_COMPLETED
        elif dn_create_date:
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
            DNStage.CLOSED: "🔒",
            DNStage.UNKNOWN: "❓"
        }
        return icons.get(stage, "❓")
    
    # ==========================================================
    # TIMELINE GENERATION (FIXED)
    # ==========================================================
    
    def _get_timeline(self, dn_create_date: Any, good_issue_date: Any, pod_date: Any) -> Dict:
        """Get timeline events"""
        events = []
        
        if dn_create_date:
            events.append({
                "stage": "DN Created",
                "date": dn_create_date,
                "icon": "📄"
            })
        
        if good_issue_date:
            events.append({
                "stage": "PGI / Dispatched",
                "date": good_issue_date,
                "icon": "🚚"
            })
        
        if pod_date:
            events.append({
                "stage": "POD Received",
                "date": pod_date,
                "icon": "📋"
            })
        
        return {
            "events": events,
            "total_events": len(events)
        }
    
    # ==========================================================
    # RECOMMENDATIONS (ENHANCED)
    # ==========================================================
    
    def _get_recommendations(self, pgi_completed: bool, pod_received: bool,
                               pending_pod_aging: int, delay_bucket: DelayBucket,
                               total_value: float, risk_level: DNRiskLevel) -> List[str]:
        """Get actionable recommendations"""
        recommendations = []
        
        if not pgi_completed:
            recommendations.append("⚠️ Pending PGI - Coordinate with warehouse for immediate dispatch")
        
        if pgi_completed and not pod_received:
            if pending_pod_aging > 14:
                recommendations.append(f"📋 CRITICAL: POD pending for {pending_pod_aging} days - Escalate to management")
            elif pending_pod_aging > 7:
                recommendations.append(f"📋 URGENT: POD pending for {pending_pod_aging} days - Send escalation notice")
            elif pending_pod_aging > 3:
                recommendations.append(f"📋 POD pending for {pending_pod_aging} days - Send reminder to customer")
            else:
                recommendations.append("📋 POD pending - Send reminder within 24 hours")
        
        if delay_bucket in [DelayBucket.VERY_LATE, DelayBucket.CRITICAL]:
            recommendations.append("🚨 Escalate to regional manager for immediate intervention")
        
        if risk_level in [DNRiskLevel.CRITICAL, DNRiskLevel.HIGH]:
            recommendations.append("💰 High risk DN - Prioritize resolution")
        
        if total_value > 5_000_000:
            recommendations.append("💰 High value DN - Ensure proper documentation")
        
        if not recommendations:
            recommendations.append("✅ DN is on track - No action required")
        
        return recommendations
    
    # ==========================================================
    # MAIN DN INTELLIGENCE METHOD (FIXED)
    # ==========================================================
    
    def get_complete_dn_intelligence(self, dn_no: str) -> Dict[str, Any]:
        """
        Get complete intelligence for a DN.
        
        FIXES APPLIED:
        - DN number normalization with multiple search strategies
        - Flexible column mapping for all fields
        - Flexible PGI/POD status mapping
        - Fixed product code bug (uses first record)
        - Defensive business rule calculations
        """
        start_time = time.time()
        
        # ==========================================================
        # FIX #1: DN NUMBER NORMALIZATION
        # ==========================================================
        if not dn_no:
            self._audit_log("error", dn_no, "Missing DN number")
            return {
                "success": False,
                "response_type": "error",
                "error": "Please provide a DN number",
                "dn_no": dn_no
            }
        
        # Log raw input for debugging
        logger.info(f"🔍 DN Search Raw Input: '{dn_no}' (type: {type(dn_no).__name__})")
        
        # ==========================================================
        # MULTIPLE SEARCH STRATEGIES
        # ==========================================================
        records = self._find_dn_records(dn_no)
        
        if not records:
            logger.warning(f"❌ No records found for DN: {dn_no}")
            self.query_stats["dn_not_found"] += 1
            self._audit_log("not_found", dn_no, "DN not in database")
            return {
                "success": False,
                "response_type": "error",
                "error": f"DN {dn_no} not found",
                "dn_no": dn_no,
                "execution_time_ms": round((time.time() - start_time) * 1000, 2)
            }
        
        first_record = records[0]
        
        # Log found record details for debugging
        logger.info(f"📊 Found {len(records)} records for DN")
        logger.info(f"   Dealer field: {self._get_field_value(first_record, 'customer_name', 'N/A')}")
        logger.info(f"   City field: {self._get_field_value(first_record, 'ship_to_city', 'N/A')}")
        logger.info(f"   Warehouse field: {self._get_field_value(first_record, 'warehouse', 'N/A')}")
        logger.info(f"   PGI Status: {self._get_field_value(first_record, 'pgi_status', 'N/A')}")
        logger.info(f"   POD Status: {self._get_field_value(first_record, 'pod_status', 'N/A')}")
        
        # ==========================================================
        # FIX #3 & #4: FLEXIBLE STATUS CHECKING
        # ==========================================================
        pgi_completed = self._is_pgi_completed(first_record)
        pod_received = self._is_pod_received(first_record)
        
        logger.info(f"   PGI Completed: {pgi_completed}")
        logger.info(f"   POD Received: {pod_received}")
        
        # ==========================================================
        # FIX #2: FLEXIBLE FIELD EXTRACTION
        # ==========================================================
        dealer = self._get_field_value(first_record, "customer_name", "Unknown Dealer")
        city = self._get_field_value(first_record, "ship_to_city", "Unknown City")
        warehouse = self._get_field_value(first_record, "warehouse", "Unknown Warehouse")
        
        # ==========================================================
        # DATE EXTRACTION
        # ==========================================================
        dn_create_date = self._get_field_value(first_record, "dn_create_date")
        good_issue_date = self._get_field_value(first_record, "good_issue_date")
        pod_date = self._get_field_value(first_record, "pod_date")
        
        # ==========================================================
        # TWO-PASS PRODUCT AGGREGATION
        # ==========================================================
        products = []
        total_qty = 0
        total_value = 0
        divisions = set()
        
        for record in records:
            # FIX #6: Use proper product code extraction
            product_code = self._get_product_code(record)
            
            qty_str = self._get_field_value(record, "dn_qty", "0")
            value_str = self._get_field_value(record, "dn_amount", "0")
            
            try:
                qty = float(qty_str) if qty_str else 0
            except (ValueError, TypeError):
                qty = 0
            
            try:
                value = float(value_str) if value_str else 0
            except (ValueError, TypeError):
                value = 0
            
            total_qty += qty
            total_value += value
            
            division = self._get_division(product_code)
            divisions.add(division)
            
            products.append({
                "product": product_code,
                "qty": qty,
                "value": value,
                "division": division
            })
        
        # Calculate percentages
        for p in products:
            p["percentage_of_dn"] = (p["value"] / total_value * 100) if total_value > 0 else 0
        
        # Determine division (use most common or first)
        division = list(divisions)[0] if divisions else "Unknown"
        
        # ==========================================================
        # AGING CALCULATIONS (FIX #5)
        # ==========================================================
        delivery_aging = self._calculate_delivery_aging(first_record)
        pending_delivery_aging = self._calculate_pending_delivery_aging(first_record)
        pod_aging = self._calculate_pod_aging(first_record)
        pending_pod_aging = self._calculate_pending_pod_aging(first_record)
        
        # Determine stage
        stage = self._determine_dn_stage(
            pgi_completed=pgi_completed,
            pod_received=pod_received,
            good_issue_date=good_issue_date,
            dn_create_date=dn_create_date
        )
        stage_icon = self._get_stage_icon(stage)
        
        # Get delivery status
        status = self._get_delivery_status(pgi_completed, pod_received)
        status_icon = self._get_status_icon(status)
        
        # Calculate max delay
        max_delay = max(delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging)
        delay_bucket = self._get_delay_bucket(max_delay)
        delay_icon = self._get_delay_icon(delay_bucket)
        
        # Calculate scores
        health_score = self._calculate_health_score(
            delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging
        )
        risk_score = self._calculate_risk_score(max_delay, total_value)
        
        # Determine risk level
        if risk_score >= 70:
            risk_level = DNRiskLevel.CRITICAL
            risk_icon = "💀"
        elif risk_score >= 50:
            risk_level = DNRiskLevel.HIGH
            risk_icon = "🚨"
        elif risk_score >= 30:
            risk_level = DNRiskLevel.MEDIUM
            risk_icon = "⚠️"
        else:
            risk_level = DNRiskLevel.LOW
            risk_icon = "✅"
        
        # Aging bucket
        if max_delay <= 1:
            aging_bucket = "Current"
        elif max_delay <= 3:
            aging_bucket = "Warning"
        elif max_delay <= 7:
            aging_bucket = "Late"
        elif max_delay <= 15:
            aging_bucket = "Very Late"
        else:
            aging_bucket = "Critical"
        
        # SLA breach flag
        sla_breach = max_delay > 3  # Simple SLA: >3 days is breach
        
        # Exception flag
        exception_flag = risk_level in [DNRiskLevel.CRITICAL, DNRiskLevel.HIGH] or max_delay > 15
        
        # Priority level
        if exception_flag and risk_level == DNRiskLevel.CRITICAL:
            priority_level = PriorityLevel.CRITICAL
        elif exception_flag and risk_level == DNRiskLevel.HIGH:
            priority_level = PriorityLevel.HIGH
        elif max_delay > 7:
            priority_level = PriorityLevel.MEDIUM
        else:
            priority_level = PriorityLevel.LOW
        
        # Timeline
        timeline = self._get_timeline(dn_create_date, good_issue_date, pod_date)
        
        # Recommendations
        recommendations = self._get_recommendations(
            pgi_completed=pgi_completed,
            pod_received=pod_received,
            pending_pod_aging=pending_pod_aging,
            delay_bucket=delay_bucket,
            total_value=total_value,
            risk_level=risk_level
        )
        
        # ==========================================================
        # BUILD RESPONSE
        # ==========================================================
        elapsed_ms = (time.time() - start_time) * 1000
        
        result = {
            "success": True,
            "response_type": "dn_intelligence",
            "execution_time_ms": round(elapsed_ms, 2),
            "data": {
                "dn_no": dn_no,
                "dealer": dealer,
                "city": city,
                "warehouse": warehouse,
                "division": division,
                "stage": stage.value,
                "stage_icon": stage_icon,
                "status": status,
                "status_icon": status_icon,
                "products": products,
                "total_qty": round(total_qty, 2),
                "total_value": round(total_value, 2),
                "product_count": len(products),
                "division_count": len(divisions),
                "pgi_date": good_issue_date,
                "pod_date": pod_date,
                "aging": {
                    "delivery_aging": delivery_aging,
                    "pending_delivery_aging": pending_delivery_aging,
                    "pod_aging": pod_aging,
                    "pending_pod_aging": pending_pod_aging
                },
                "sla": {
                    "delivery_status": "Delayed" if max_delay > 3 else "On Time",
                    "delivery_icon": "🔴" if max_delay > 3 else "✅",
                    "pod_status": "Delayed" if pending_pod_aging > 7 else "On Time",
                    "pod_icon": "🔴" if pending_pod_aging > 7 else "✅"
                },
                "delay_bucket": delay_bucket.value,
                "delay_icon": delay_icon,
                "health_score": health_score,
                "risk_score": risk_score,
                "risk_level": risk_level.value,
                "risk_icon": risk_icon,
                "aging_bucket": aging_bucket,
                "sla_breach": sla_breach,
                "exception_flag": exception_flag,
                "priority_level": priority_level.value,
                "timeline": timeline,
                "recommendations": recommendations
            }
        }
        
        # Update stats
        self.query_stats["total_queries"] += 1
        self.query_stats["avg_response_time_ms"] = (
            (self.query_stats["avg_response_time_ms"] * (self.query_stats["total_queries"] - 1) + elapsed_ms)
            / self.query_stats["total_queries"]
        )
        
        # Audit log
        self._audit_log("success", dn_no, f"Found {len(records)} records", {
            "dealer": dealer,
            "total_value": total_value,
            "status": status,
            "risk_level": risk_level.value,
            "response_time_ms": round(elapsed_ms, 2)
        })
        
        logger.info(f"⚡ DN {dn_no} - Response Time: {elapsed_ms:.2f}ms | Records: {len(records)} | Status: {status}")
        
        return result
    
    # ==========================================================
    # ADDITIONAL SERVICE METHODS
    # ==========================================================
    
    def get_dn_timeline(self, dn_no: str) -> Dict[str, Any]:
        """Get DN timeline only"""
        intelligence = self.get_complete_dn_intelligence(dn_no)
        if intelligence.get("success"):
            return {"timeline": intelligence["data"]["timeline"]}
        return {"error": intelligence.get("error", "DN not found")}
    
    def get_dn_products(self, dn_no: str) -> Dict[str, Any]:
        """Get DN products only"""
        intelligence = self.get_complete_dn_intelligence(dn_no)
        if intelligence.get("success"):
            return {
                "products": intelligence["data"]["products"],
                "total_qty": intelligence["data"]["total_qty"],
                "total_value": intelligence["data"]["total_value"]
            }
        return {"error": intelligence.get("error", "DN not found")}
    
    def get_dn_aging(self, dn_no: str) -> Dict[str, Any]:
        """Get DN aging only"""
        intelligence = self.get_complete_dn_intelligence(dn_no)
        if intelligence.get("success"):
            return {"aging": intelligence["data"]["aging"]}
        return {"error": intelligence.get("error", "DN not found")}
    
    def get_pending_pods(self, limit: int = 20) -> Dict[str, Any]:
        """Get DNs with pending POD"""
        try:
            all_records = self.db.query(DeliveryReport).all()
            
            pending_dns = []
            for record in all_records:
                if self._is_pgi_completed(record) and not self._is_pod_received(record):
                    dn_no = self._get_field_value(record, "dn_no", "")
                    dealer = self._get_field_value(record, "customer_name", "Unknown")
                    value = self._get_field_value(record, "dn_amount", 0)
                    
                    try:
                        value_float = float(value) if value else 0
                    except (ValueError, TypeError):
                        value_float = 0
                    
                    pending_days = self._calculate_pending_pod_aging(record)
                    
                    pending_dns.append({
                        "dn_no": dn_no,
                        "dealer": dealer,
                        "value": value_float,
                        "pending_days": pending_days
                    })
            
            pending_dns.sort(key=lambda x: x["pending_days"], reverse=True)
            
            return {
                "success": True,
                "pending_pods": pending_dns[:limit],
                "total_pending": len(pending_dns)
            }
        except Exception as e:
            logger.error(f"Error getting pending PODs: {e}")
            return {"success": False, "error": str(e)}
    
    def get_pending_pgi(self, limit: int = 20) -> Dict[str, Any]:
        """Get DNs with pending PGI"""
        try:
            all_records = self.db.query(DeliveryReport).all()
            
            pending_dns = []
            for record in all_records:
                if not self._is_pgi_completed(record):
                    dn_no = self._get_field_value(record, "dn_no", "")
                    dealer = self._get_field_value(record, "customer_name", "Unknown")
                    value = self._get_field_value(record, "dn_amount", 0)
                    
                    try:
                        value_float = float(value) if value else 0
                    except (ValueError, TypeError):
                        value_float = 0
                    
                    pending_days = self._calculate_pending_delivery_aging(record)
                    
                    pending_dns.append({
                        "dn_no": dn_no,
                        "dealer": dealer,
                        "value": value_float,
                        "pending_days": pending_days
                    })
            
            pending_dns.sort(key=lambda x: x["pending_days"], reverse=True)
            
            return {
                "success": True,
                "pending_pgi": pending_dns[:limit],
                "total_pending": len(pending_dns)
            }
        except Exception as e:
            logger.error(f"Error getting pending PGI: {e}")
            return {"success": False, "error": str(e)}
    
    # ==========================================================
    # HEALTH CHECK & STATS
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for service monitoring"""
        return {
            "service": "logistics_query_service",
            "version": "4.1",
            "status": "healthy",
            "components": {
                "database": self._check_database(),
                "cache": self.cache is not None,
                "service": True
            },
            "metrics": self.get_query_stats(),
            "audit_log_size": len(self.audit_log)
        }
    
    def _check_database(self) -> bool:
        """Check database connectivity"""
        try:
            self.db.execute("SELECT 1")
            return True
        except Exception:
            return False
    
    def get_query_stats(self) -> Dict:
        """Get query performance statistics"""
        return {
            "total_queries": self.query_stats["total_queries"],
            "avg_response_time_ms": round(self.query_stats["avg_response_time_ms"], 2),
            "cache_hits": self.query_stats["cache_hits"],
            "errors": self.query_stats["errors"],
            "dn_not_found": self.query_stats["dn_not_found"],
            "cache_hit_rate": round(
                self.query_stats["cache_hits"] / max(1, self.query_stats["total_queries"]) * 100, 1
            )
        }
    
    def _audit_log(self, event_type: str, dn_no: str, message: str, extra: Dict = None):
        """Audit logging for troubleshooting"""
        audit_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "dn_no": dn_no,
            "message": message,
            "extra": extra or {}
        }
        self.audit_log.append(audit_entry)
        
        # Keep only last 1000 entries
        if len(self.audit_log) > 1000:
            self.audit_log = self.audit_log[-1000:]
    
    def get_audit_log(self, limit: int = 100) -> List[Dict]:
        """Get recent audit log entries"""
        return self.audit_log[-limit:]


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_logistics_query_service(db: Session, cache_service=None) -> LogisticsQueryService:
    """Factory function for LogisticsQueryService"""
    return LogisticsQueryService(db, cache_service)
