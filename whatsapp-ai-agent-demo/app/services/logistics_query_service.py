# ==========================================================
# FILE: app/services/logistics_query_service.py (ENTERPRISE v4.0)
# ==========================================================
# LOGISTICS QUERY SERVICE - FULLY IMPROVED
# - Fixed: DN Search Normalization
# - Fixed: Safe Null Handling for all fields
# - Fixed: Removed delivery_date dependency (not in model)
# - Fixed: Product aggregation with two-pass calculation
# - Fixed: Dealer performance using DN count (not row count)
# - Added: Standardized response contract
# - Added: Enhanced cache strategy with TTL by type
# - Added: Business KPIs (aging_bucket, sla_breach, exception_flag)
# - Added: Audit logging
# - Added: Health check endpoint
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


class SLABucket(str, Enum):
    ON_TIME = "On Time"
    BREACHED = "Breached"


class PriorityLevel(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


@dataclass
class DNRiskAssessment:
    level: DNRiskLevel
    score: int
    reasons: List[str]
    icon: str
    action_required: str


class LogisticsQueryService:
    """
    Enterprise Logistics Query Service v4.0
    Handles all DN-related database queries and intelligence
    """
    
    def __init__(self, db: Session, cache_service=None):
        self.db = db
        self.cache = cache_service
        self.business_rules = BusinessRulesService()
        
        # Cache TTL by type (Priority 6)
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
        
        # Audit log (Priority 8)
        self.audit_log = []
        
        logger.info("=" * 60)
        logger.info("✅ Logistics Query Service v4.0 initialized")
        logger.info("   Cache TTL: DN=60s, Dealer=300s, KPI=300s")
        logger.info("=" * 60)
    
    # ==========================================================
    # DN 360 INTELLIGENCE (COMPLETE FIXED VERSION)
    # ==========================================================
    
    def get_complete_dn_intelligence(self, dn_no: str) -> Dict[str, Any]:
        """
        Get complete intelligence for a DN.
        Aggregates ALL products in the DN (not just first row).
        
        FIXES:
        - DN normalization (strip whitespace)
        - Safe null handling for all fields
        - Removed delivery_date dependency
        - Two-pass product aggregation for correct percentages
        - Added business KPIs
        """
        start_time = time.time()
        
        # ==========================================================
        # Priority 1 - Fix 1: DN Search Normalization
        # ==========================================================
        if not dn_no:
            self._audit_log("error", dn_no, "Missing DN number")
            return {"error": "Please provide a DN number"}
        
        dn_no = str(dn_no).strip()
        logger.info(f"🔍 DN Search: '{dn_no}'")
        
        # Check cache with type-specific TTL
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
            
            # ==========================================================
            # Priority 1 - Fix 2: Enhanced Query Diagnostics
            # ==========================================================
            logger.info(f"📊 DN Query Results:")
            logger.info(f"   DN={dn_no}")
            logger.info(f"   Records Found={len(records)}")
            
            if records:
                first = records[0]
                logger.info(f"   Dealer={getattr(first, 'customer_name', 'None')}")
                logger.info(f"   City={getattr(first, 'ship_to_city', 'None')}")
                logger.info(f"   Warehouse={getattr(first, 'warehouse', 'None')}")
                logger.info(f"   PGI Status={getattr(first, 'pgi_status', 'None')}")
                logger.info(f"   POD Status={getattr(first, 'pod_status', 'None')}")
            else:
                logger.warning(f"❌ No records found for DN: {dn_no}")
                self.query_stats["dn_not_found"] += 1
                self._audit_log("not_found", dn_no, "DN not in database")
                return {"error": f"DN {dn_no} not found"}
            
            first_record = records[0]
            
            # ==========================================================
            # Priority 3 - Fix 5: Two-Pass Product Aggregation
            # ==========================================================
            # PASS 1: Calculate totals
            products_raw = []
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
                
                products_raw.append({
                    "product": product_code,
                    "qty": qty,
                    "value": value,
                    "division": division
                })
            
            # PASS 2: Calculate percentages
            products = []
            for p in products_raw:
                p["percentage_of_dn"] = (p["value"] / total_value * 100) if total_value > 0 else 0
                products.append(p)
            
            # ==========================================================
            # Priority 3 - Fix 3: Safe Null Handling
            # ==========================================================
            dealer = self._safe_get(first_record, 'customer_name', 'Unknown Dealer')
            city = self._safe_get(first_record, 'ship_to_city', 'Unknown City')
            warehouse = self._safe_get(first_record, 'warehouse', 'Unknown Warehouse')
            division = self._get_division(product_code) if products else "Unknown"
            
            # ==========================================================
            # Priority 3 - Fix 4: NO delivery_date - use good_issue_date
            # ==========================================================
            # delivery_date does NOT exist in DeliveryReport model
            # Using good_issue_date as delivery proxy
            good_issue_date = self._safe_get(first_record, 'good_issue_date', None)
            pod_date = self._safe_get(first_record, 'pod_date', None)
            dn_create_date = self._safe_get(first_record, 'dn_create_date', None)
            pgi_status = self._safe_get(first_record, 'pgi_status', 'Pending')
            pod_status = self._safe_get(first_record, 'pod_status', 'Pending')
            
            # Calculate aging metrics
            delivery_aging = self.business_rules.calculate_delivery_aging(first_record)
            pending_delivery_aging = self.business_rules.calculate_pending_delivery_aging(first_record)
            pod_aging = self.business_rules.calculate_pod_aging(first_record)
            pending_pod_aging = self.business_rules.calculate_pending_pod_aging(first_record)
            
            # Determine DN stage (using good_issue_date instead of delivery_date)
            stage = self._determine_dn_stage_safe(
                pod_status=pod_status,
                good_issue_date=good_issue_date,
                pgi_status=pgi_status,
                dn_create_date=dn_create_date
            )
            stage_icon = self._get_stage_icon(stage)
            
            # Calculate SLA status
            delivery_sla = self.business_rules.check_delivery_sla(delivery_aging)
            pod_sla = self.business_rules.check_pod_sla(pod_aging)
            
            # Calculate delay bucket
            max_delay = max(delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging)
            delay_bucket = self.business_rules.get_delay_bucket(max_delay)
            
            # Get delivery status
            status = self.business_rules.get_delivery_status(pgi_status, pod_status)
            
            # Calculate health score
            health_score = self.business_rules.calculate_health_score(
                delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging
            )
            
            # Calculate risk assessment
            risk_score = self.business_rules.calculate_risk_score(max_delay, total_value)
            
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
            
            # ==========================================================
            # Priority 7 - Add Business KPIs
            # ==========================================================
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
            sla_breach = delivery_sla.status == "Delayed" or pod_sla.status == "Delayed"
            
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
            
            # Get timeline (using good_issue_date instead of delivery_date)
            timeline = self._get_timeline_safe(
                dn_create_date=dn_create_date,
                good_issue_date=good_issue_date,
                pod_date=pod_date
            )
            
            # Get recommendations
            recommendations = self._get_recommendations_enhanced(
                pgi_status=pgi_status,
                pod_status=pod_status,
                pending_pod_aging=pending_pod_aging,
                delay_bucket=delay_bucket,
                total_value=total_value,
                risk_level=risk_level
            )
            
            # ==========================================================
            # Priority 5 - Standardized Response Contract
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
                    "status": status.value,
                    "status_icon": self.business_rules.get_status_icon(status),
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
                        "delivery_status": delivery_sla.status,
                        "delivery_icon": delivery_sla.icon,
                        "pod_status": pod_sla.status,
                        "pod_icon": pod_sla.icon
                    },
                    "delay_bucket": delay_bucket.value if delay_bucket else "Unknown",
                    "delay_icon": self.business_rules.get_delay_icon(delay_bucket) if delay_bucket else "⚪",
                    "health_score": health_score,
                    "risk_score": risk_score,
                    "risk_level": risk_level.value,
                    "risk_icon": risk_icon,
                    # Business KPIs (Priority 7)
                    "aging_bucket": aging_bucket,
                    "sla_breach": sla_breach,
                    "exception_flag": exception_flag,
                    "priority_level": priority_level.value,
                    "timeline": timeline,
                    "recommendations": recommendations
                }
            }
            
            # Cache with DN-specific TTL (60 seconds)
            if self.cache and self.cache.enabled:
                ttl = self.cache_ttl.get("dn_intelligence", 60)
                self.cache.set(cache_key, result, ttl=ttl)
                logger.debug(f"💾 Cached DN {dn_no} with TTL {ttl}s")
            
            # Update stats
            self.query_stats["total_queries"] += 1
            self.query_stats["avg_response_time_ms"] = (
                (self.query_stats["avg_response_time_ms"] * (self.query_stats["total_queries"] - 1) + elapsed_ms) 
                / self.query_stats["total_queries"]
            )
            
            # ==========================================================
            # Priority 8 - Audit Logging
            # ==========================================================
            self._audit_log("success", dn_no, f"Found {len(records)} records", {
                "dealer": dealer,
                "total_value": total_value,
                "status": status.value,
                "risk_level": risk_level.value,
                "response_time_ms": round(elapsed_ms, 2)
            })
            
            logger.info(f"⚡ DN {dn_no} - Response Time: {elapsed_ms:.2f}ms | Records: {len(records)}")
            
            return result
            
        except Exception as e:
            logger.exception(f"DN Intelligence Failed for {dn_no}: {e}")
            self.query_stats["errors"] += 1
            self._audit_log("error", dn_no, str(e))
            return {
                "success": False,
                "response_type": "error",
                "error": str(e),
                "dn_no": dn_no,
                "execution_time_ms": round((time.time() - start_time) * 1000, 2)
            }
    
    # ==========================================================
    # SAFE HELPER METHODS
    # ==========================================================
    
    def _safe_get(self, record, field: str, default: Any = None) -> Any:
        """Safely get attribute from record"""
        return getattr(record, field, default) if record else default
    
    def _determine_dn_stage_safe(self, pod_status: str, good_issue_date: Any,
                                   pgi_status: str, dn_create_date: Any) -> DNStage:
        """Determine DN stage without delivery_date"""
        if pod_status == "Received":
            return DNStage.CLOSED
        elif pod_status == "Pending" and good_issue_date:
            return DNStage.POD_PENDING
        elif good_issue_date:
            return DNStage.DELIVERED  # Using good_issue_date as delivery proxy
        elif pgi_status == "Completed":
            return DNStage.PGI_COMPLETED
        elif dn_create_date:
            return DNStage.AWAITING_PGI
        else:
            return DNStage.DN_CREATED
    
    def _get_timeline_safe(self, dn_create_date: Any, good_issue_date: Any, pod_date: Any) -> Dict:
        """Get timeline without delivery_date"""
        events = []
        
        if dn_create_date:
            events.append({"stage": "DN Created", "date": dn_create_date, "icon": "📄"})
        if good_issue_date:
            events.append({"stage": "PGI / Dispatched", "date": good_issue_date, "icon": "🚚"})
        if pod_date:
            events.append({"stage": "POD Received", "date": pod_date, "icon": "📋"})
        
        return {"events": events, "total_events": len(events)}
    
    def _get_recommendations_enhanced(self, pgi_status: str, pod_status: str,
                                        pending_pod_aging: int, delay_bucket: Any,
                                        total_value: float, risk_level: DNRiskLevel) -> List[str]:
        """Enhanced recommendations with priority levels"""
        recommendations = []
        
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
        
        if delay_bucket and delay_bucket.value in ["Severe", "Critical"]:
            recommendations.append("🚨 Escalate to regional manager for immediate intervention")
        
        if risk_level in [DNRiskLevel.CRITICAL, DNRiskLevel.HIGH]:
            recommendations.append("💰 High risk DN - Prioritize resolution")
        
        if total_value > 5_000_000:
            recommendations.append("💰 High value DN - Ensure proper documentation")
        
        if not recommendations:
            recommendations.append("✅ DN is on track - No action required")
        
        return recommendations
    
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
    
    def _audit_log(self, event_type: str, dn_no: str, message: str, extra: Dict = None):
        """Priority 8 - Audit logging"""
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
    
    # ==========================================================
    # PENDING & DELAYED DNS (FIXED with DN count not row count)
    # ==========================================================
    
    def get_pending_dns(self, limit: int = 20) -> List[Dict]:
        """Get pending DNs (PGI not completed)"""
        try:
            # Priority 4 - Fix: Use DISTINCT on dn_no
            records = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.pgi_status != "Completed"
            ).distinct().order_by(
                DeliveryReport.dn_create_date
            ).limit(limit).all()
            
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
    
    def get_pending_pods(self, limit: int = 20) -> List[Dict]:
        """Get DNs with pending POD"""
        try:
            records = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).distinct().order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(limit).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "value": float(r.dn_amount or 0),
                "pending_days": self.business_rules.calculate_pending_pod_aging(r)
            } for r in records]
        except Exception as e:
            logger.error(f"Error getting pending PODs: {e}")
            return []
    
    # ==========================================================
    # DEALER PERFORMANCE (FIXED with DN count)
    # ==========================================================
    
    def get_dealer_delivery_performance(self, dealer_name: str) -> Dict:
        """Get delivery performance for a dealer"""
        try:
            # Priority 4 - Fix: Use distinct DN count
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name == dealer_name
            ).all()
            
            if not records:
                return {"error": f"No records found for dealer '{dealer_name}'"}
            
            # Get unique DNs
            unique_dns = set()
            completed_dns = set()
            pod_received_dns = set()
            
            for r in records:
                unique_dns.add(r.dn_no)
                if r.pgi_status == "Completed":
                    completed_dns.add(r.dn_no)
                if r.pod_status == "Received":
                    pod_received_dns.add(r.dn_no)
            
            total_dns = len(unique_dns)
            completed = len(completed_dns)
            pod_received = len(pod_received_dns)
            
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
    # HEALTH CHECK (Priority 9)
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for service monitoring"""
        return {
            "service": "logistics_query_service",
            "version": "4.0",
            "status": "healthy",
            "components": {
                "database": self._check_database(),
                "cache": self._check_cache(),
                "business_rules": self._check_business_rules(),
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
    
    def _check_cache(self) -> bool:
        """Check cache availability"""
        return self.cache is not None and self.cache.enabled if hasattr(self.cache, 'enabled') else False
    
    def _check_business_rules(self) -> bool:
        """Check business rules service"""
        return self.business_rules is not None
    
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
    
    def get_audit_log(self, limit: int = 100) -> List[Dict]:
        """Get recent audit log entries"""
        return self.audit_log[-limit:]


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_logistics_query_service(db: Session, cache_service=None) -> LogisticsQueryService:
    return LogisticsQueryService(db, cache_service)
