# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v15.0 - DN-CENTRIC)
# ==========================================================
# DN-CENTRIC INTELLIGENCE ENGINE:
# - Treats DN Number as primary business entity
# - Aggregates all products/rows for complete DN view
# - Calculates proper aging (DN Aging, Dispatch Aging, Delivery Aging, POD Aging)
# - Integrates dealer context with DN view
# - Enriches AI context with complete business data
# - Risk intelligence with actionable recommendations
# - Performance optimized with SQL aggregation
# - DN Health Score & SLA Monitoring
# - Exception Flags & Enhanced Intent Detection
# ==========================================================

import re
import time
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
from collections import deque, defaultdict

from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
from loguru import logger

from app.config import config
from app.models import DeliveryReport

# ==========================================================
# IMPORT GROQ PROVIDER
# ==========================================================

try:
    from app.services.ai_provider_service import get_ai_provider_service
    AI_PROVIDER_AVAILABLE = True
except ImportError as e:
    logger.error(f"Failed to import AI provider: {e}")
    AI_PROVIDER_AVAILABLE = False


# ==========================================================
# SLA CONFIGURATION
# ==========================================================

class SLAMetrics:
    """SLA configuration for logistics KPIs"""
    DISPATCH_SLA_DAYS = 3
    DELIVERY_SLA_DAYS = 5
    POD_SLA_DAYS = 7
    
    @staticmethod
    def get_sla_status(actual_days: int, sla_days: int) -> Tuple[str, str]:
        """Returns (status, icon)"""
        if actual_days <= sla_days:
            return "Within SLA", "✅"
        elif actual_days <= sla_days * 1.5:
            return "Near Breach", "⚠️"
        else:
            return "Breach", "🔴"


# ==========================================================
# DN-CENTRIC DATABASE SERVICE
# ==========================================================

class DNCentricDatabaseService:
    """
    DN-Centric Intelligence Engine
    Treats DN Number as primary business entity, not individual product rows
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    # ==========================================================
    # HELPER METHODS FOR RISK CALCULATION
    # ==========================================================
    
    def _calculate_pod_risk(self, primary, pod_aging_days: int, delivery_date, pgi_date) -> Tuple[int, int]:
        """Calculate POD risk with proper aging for pending PODs"""
        pod_risk_score = 0
        pending_pod_aging = 0
        
        if primary.pod_status == "Pending":
            # Calculate aging from delivery date or PGI date
            if delivery_date:
                pending_pod_aging = (datetime.now().date() - delivery_date).days
            elif pgi_date:
                pending_pod_aging = (datetime.now().date() - pgi_date).days
            
            if pending_pod_aging > 10:
                pod_risk_score += 20
            elif pending_pod_aging > 5:
                pod_risk_score += 10
                
        elif primary.pod_status == "Received":
            if pod_aging_days > 10:
                pod_risk_score += 20
            elif pod_aging_days > 5:
                pod_risk_score += 10
                
        return pod_risk_score, pending_pod_aging
    
    def _calculate_dn_health_score(self, dispatch_aging: int, delivery_aging: int, 
                                    pod_aging: int, pod_status: str, pending_pod_aging: int = 0) -> int:
        """Calculate DN Health Score (0-100)"""
        score = 100
        
        # Dispatch delay penalty (max 30 points)
        if dispatch_aging > 15:
            score -= 30
        elif dispatch_aging > 7:
            score -= 15
        elif dispatch_aging > 3:
            score -= 5
        
        # Delivery delay penalty (max 30 points)
        if delivery_aging > 10:
            score -= 30
        elif delivery_aging > 5:
            score -= 15
        elif delivery_aging > 2:
            score -= 5
        
        # POD penalty (max 40 points)
        if pod_status == "Pending":
            aging_to_use = pending_pod_aging if pending_pod_aging > 0 else pod_aging
            if aging_to_use > 10:
                score -= 40
            elif aging_to_use > 5:
                score -= 25
            elif aging_to_use > 2:
                score -= 10
        elif pod_status == "Received" and pod_aging > 7:
            score -= 10
        
        return max(0, min(100, score))
    
    def _get_exception_flags(self, data: Dict) -> List[str]:
        """Generate exception flags for DN report"""
        flags = []
        
        if data.get('total_value', 0) > 5_000_000:
            flags.append("💰 HIGH VALUE DN")
        if data.get('dispatch_aging_days', 0) > 7:
            flags.append("🚨 DELAYED DISPATCH")
        if data.get('pod_status') == "Pending" and data.get('pending_pod_aging', 0) > 10:
            flags.append("📋 POD OVERDUE")
        if data.get('dealer_summary', {}).get('health_score', 100) < 40:
            flags.append("🏪 DEALER RISK")
        if data.get('delivery_aging_days', 0) > 10:
            flags.append("🚛 DELIVERY DELAY")
        
        return flags
    
    def _get_dealer_aggregated_metrics(self, dealer_name: str) -> Dict:
        """Single aggregated query for dealer metrics (replaces .all() queries)"""
        result = self.db.query(
            func.count(DeliveryReport.dn_no).label("total_dns"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").label("delivered_dns"),
            func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status == "Completed").label("delivered_value"),
            func.count(DeliveryReport.dn_no).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).label("pod_pending_dns")
        ).filter(DeliveryReport.customer_name == dealer_name).first()
        
        return {
            "total_dns": result.total_dns or 0,
            "total_value": float(result.total_value or 0),
            "delivered_dns": result.delivered_dns or 0,
            "delivered_value": float(result.delivered_value or 0),
            "pod_pending_dns": result.pod_pending_dns or 0
        }
    
    # ==========================================================
    # CORE DN INTELLIGENCE - AGGREGATES ALL ROWS FOR A DN
    # ==========================================================
    
    def get_dn_complete_intelligence(self, dn_number: str) -> Dict[str, Any]:
        """
        DN-Centric Intelligence: Aggregates ALL rows for a single DN
        Returns complete DN view with:
        - All products in DN
        - Total units, total value
        - Proper aging calculations
        - Dealer context
        - Risk analysis
        - DN Health Score
        - SLA Status
        - Exception Flags
        """
        try:
            # STEP 1: Get ALL rows for this DN (not just first)
            all_records = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).all()
            
            if not all_records:
                return {"success": False, "message": f"❌ DN {dn_number} not found"}
            
            # Get the primary record (first one, but we'll aggregate from all)
            primary = all_records[0]
            
            # ==========================================================
            # AGGREGATE DN DATA FROM ALL ROWS
            # ==========================================================
            
            # Total units and value
            total_units = sum(float(r.dn_qty or 0) for r in all_records)
            total_value = sum(float(r.dn_amount or 0) for r in all_records)
            
            # All products in this DN
            products = []
            product_summary = defaultdict(lambda: {"quantity": 0, "value": 0})
            for r in all_records:
                product_name = r.product or "Unknown"
                qty = float(r.dn_qty or 0)
                amt = float(r.dn_amount or 0)
                product_summary[product_name]["quantity"] += qty
                product_summary[product_name]["value"] += amt
                products.append({
                    "name": product_name,
                    "quantity": qty,
                    "value": amt
                })
            
            # Find highest quantity and value products
            highest_qty_product = max(product_summary.items(), key=lambda x: x[1]["quantity"]) if product_summary else (None, None)
            highest_value_product = max(product_summary.items(), key=lambda x: x[1]["value"]) if product_summary else (None, None)
            
            # ==========================================================
            # PROPER AGING CALCULATIONS (KPIs management needs)
            # ==========================================================
            
            # 1. DN Aging = Today - DN Creation Date
            dn_aging_days = 0
            dn_create_date = None
            if primary.dn_create_date:
                if isinstance(primary.dn_create_date, datetime):
                    dn_create_date = primary.dn_create_date.date()
                else:
                    dn_create_date = primary.dn_create_date
                dn_aging_days = (datetime.now().date() - dn_create_date).days
            
            # 2. Dispatch Aging = PGI Date - DN Creation Date
            dispatch_aging_days = 0
            pgi_date = None
            if hasattr(primary, 'good_issue_date') and primary.good_issue_date:
                if isinstance(primary.good_issue_date, datetime):
                    pgi_date = primary.good_issue_date.date()
                else:
                    pgi_date = primary.good_issue_date
                if dn_create_date and pgi_date:
                    dispatch_aging_days = (pgi_date - dn_create_date).days
            
            # 3. Delivery Aging = Delivery Date - PGI Date
            delivery_aging_days = 0
            delivery_date = None
            if primary.pgi_status == "Completed":
                if hasattr(primary, 'delivery_date') and primary.delivery_date:
                    if isinstance(primary.delivery_date, datetime):
                        delivery_date = primary.delivery_date.date()
                    else:
                        delivery_date = primary.delivery_date
                    if pgi_date and delivery_date:
                        delivery_aging_days = (delivery_date - pgi_date).days
            
            # 4. POD Aging = POD Date - Delivery Date
            pod_aging_days = 0
            pod_date = None
            if primary.pod_status == "Received":
                if hasattr(primary, 'pod_date') and primary.pod_date:
                    if isinstance(primary.pod_date, datetime):
                        pod_date = primary.pod_date.date()
                    else:
                        pod_date = primary.pod_date
                    if delivery_date and pod_date:
                        pod_aging_days = (pod_date - delivery_date).days
                    elif pgi_date and pod_date:
                        pod_aging_days = (pod_date - pgi_date).days
            
            # Calculate POD risk with proper pending POD aging
            pod_risk_score, pending_pod_aging = self._calculate_pod_risk(
                primary, pod_aging_days, delivery_date, pgi_date
            )
            
            # ==========================================================
            # DEALER CONTEXT INTEGRATION (Using Aggregated Query)
            # ==========================================================
            
            dealer_name = primary.customer_name
            dealer_metrics = self._get_dealer_aggregated_metrics(dealer_name)
            
            dealer_total_dns = dealer_metrics["total_dns"]
            dealer_delivered = dealer_metrics["delivered_dns"]
            dealer_pending = dealer_total_dns - dealer_delivered
            dealer_pod_pending = dealer_metrics["pod_pending_dns"]
            dealer_total_value = dealer_metrics["total_value"]
            dealer_pending_value = dealer_total_value - dealer_metrics["delivered_value"]
            
            dealer_delivery_rate = (dealer_delivered / dealer_total_dns) * 100 if dealer_total_dns > 0 else 0
            dealer_pod_rate = ((dealer_delivered - dealer_pod_pending) / dealer_delivered) * 100 if dealer_delivered > 0 else 0
            dealer_health_score = (dealer_delivery_rate * 0.6) + (dealer_pod_rate * 0.4)
            
            # ==========================================================
            # RISK INTELLIGENCE
            # ==========================================================
            
            risk_score = 0
            risk_factors = []
            risk_level = "LOW"
            risk_icon = "🟢"
            
            # Dispatch Risk
            if dispatch_aging_days > 15:
                risk_score += 40
                risk_factors.append(f"Dispatch delayed {dispatch_aging_days} days")
            elif dispatch_aging_days > 7:
                risk_score += 20
                risk_factors.append(f"Dispatch aging: {dispatch_aging_days} days")
            
            # Delivery Risk
            if delivery_aging_days > 10:
                risk_score += 30
                risk_factors.append(f"Delivery delayed {delivery_aging_days} days")
            elif delivery_aging_days > 5:
                risk_score += 15
                risk_factors.append(f"Delivery aging: {delivery_aging_days} days")
            
            # POD Risk (using improved calculation)
            risk_score += pod_risk_score
            if pending_pod_aging > 10:
                risk_factors.append(f"POD pending {pending_pod_aging} days")
            elif pending_pod_aging > 5:
                risk_factors.append(f"POD aging: {pending_pod_aging} days")
            
            # Dealer Risk
            if dealer_pending > 20:
                risk_score += 10
                risk_factors.append(f"Dealer has {dealer_pending} other pending DNs")
            
            if risk_score >= 70:
                risk_level = "CRITICAL"
                risk_icon = "💀"
            elif risk_score >= 50:
                risk_level = "HIGH"
                risk_icon = "🔴"
            elif risk_score >= 30:
                risk_level = "MEDIUM"
                risk_icon = "🟡"
            
            # ==========================================================
            # DN HEALTH SCORE
            # ==========================================================
            
            dn_health_score = self._calculate_dn_health_score(
                dispatch_aging_days, delivery_aging_days, 
                pod_aging_days, primary.pod_status, pending_pod_aging
            )
            
            # ==========================================================
            # SLA STATUS
            # ==========================================================
            
            dispatch_sla_status, dispatch_sla_icon = SLAMetrics.get_sla_status(
                dispatch_aging_days, SLAMetrics.DISPATCH_SLA_DAYS
            )
            delivery_sla_status, delivery_sla_icon = SLAMetrics.get_sla_status(
                delivery_aging_days, SLAMetrics.DELIVERY_SLA_DAYS
            )
            pod_sla_status, pod_sla_icon = SLAMetrics.get_sla_status(
                pending_pod_aging if primary.pod_status == "Pending" else pod_aging_days, 
                SLAMetrics.POD_SLA_DAYS
            )
            
            # ==========================================================
            # EXCEPTION FLAGS
            # ==========================================================
            
            exception_data = {
                "total_value": total_value,
                "dispatch_aging_days": dispatch_aging_days,
                "pod_status": primary.pod_status,
                "pending_pod_aging": pending_pod_aging,
                "delivery_aging_days": delivery_aging_days,
                "dealer_summary": {"health_score": dealer_health_score}
            }
            exception_flags = self._get_exception_flags(exception_data)
            
            # ==========================================================
            # FORMATTED RESPONSE
            # ==========================================================
            
            # Build product list string
            product_list = ""
            for p in list(product_summary.items())[:5]:
                product_list += f"   • {p[0]}: {p[1]['quantity']:,.0f} units (Rs {p[1]['value']:,.2f})\n"
            if len(product_summary) > 5:
                product_list += f"   • ... and {len(product_summary) - 5} more products\n"
            
            # Build exception flags string
            flags_str = ""
            if exception_flags:
                flags_str = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                flags_str += "🚨 *EXCEPTION FLAGS*\n"
                flags_str += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                for flag in exception_flags:
                    flags_str += f"   {flag}\n"
            
            response = f"""╔══════════════════════════════════════════════════════════════╗
║              📦 DN COMPLETE INTELLIGENCE REPORT                    ║
║                         {dn_number}                                   ║
╚══════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *DN SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Dealer: {dealer_name}
• City: {primary.ship_to_city or 'N/A'}
• Warehouse: {primary.warehouse or 'N/A'}
• Total Units: {total_units:,.0f}
• Total Value: Rs {total_value:,.2f}
• Products: {len(product_summary)} items
• Highest Qty Product: {highest_qty_product[0] if highest_qty_product[0] else 'N/A'} ({highest_qty_product[1]['quantity']:,.0f} units)
• Highest Value Product: {highest_value_product[0] if highest_value_product[0] else 'N/A'} (Rs {highest_value_product[1]['value']:,.2f})

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 *PRODUCT BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{product_list}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AGING ANALYSIS (KPIs)*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 📅 DN Aging (Today - Create): {dn_aging_days} days
• 🚚 Dispatch Aging (PGI - Create): {dispatch_aging_days} days
• 🚛 Delivery Aging (Delivery - PGI): {delivery_aging_days} days
• 📋 POD Aging (POD - Delivery): {pod_aging_days if primary.pod_status == 'Received' else 'Pending'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *CURRENT STATUS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Delivery Status: {'✅ DELIVERED' if primary.pgi_status == 'Completed' else '⏳ PENDING'}
• POD Status: {'✅ RECEIVED' if primary.pod_status == 'Received' else '📋 PENDING'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💚 *DN HEALTH SCORE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {dn_health_score}/100
{"✅ Excellent" if dn_health_score >= 80 else "⚠️ Needs Attention" if dn_health_score >= 50 else "🔴 Critical"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *SLA MONITORING*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Dispatch SLA ({SLAMetrics.DISPATCH_SLA_DAYS} days): {dispatch_sla_icon} {dispatch_sla_status}
• Delivery SLA ({SLAMetrics.DELIVERY_SLA_DAYS} days): {delivery_sla_icon} {delivery_sla_status}
• POD SLA ({SLAMetrics.POD_SLA_DAYS} days): {pod_sla_icon} {pod_sla_status}

{flags_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *RISK INTELLIGENCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{risk_icon} Risk Score: {risk_score}/100
• Risk Level: {risk_level}
"""
            if risk_factors:
                response += "\n*Risk Factors:*\n"
                for f in risk_factors[:3]:
                    response += f"   • {f}\n"
            
            response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏪 *DEALER CONTEXT - {dealer_name}*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {dealer_total_dns}
• Delivered: {dealer_delivered} ✅
• Pending: {dealer_pending} ⏳
• POD Pending: {dealer_pod_pending} 📋
• Total Value: Rs {dealer_total_value:,.2f}
• Health Score: {dealer_health_score:.1f}/100

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            recommendations = []
            
            if dispatch_aging_days > 10:
                recommendations.append("• 🚨 Escalate to warehouse - dispatch delay")
            if delivery_aging_days > 7:
                recommendations.append("• 🚛 Follow up with transporter")
            if primary.pod_status == "Pending" and pending_pod_aging > 7:
                recommendations.append("• 📋 Urgent: Collect POD from dealer")
            if dealer_pending > 0:
                recommendations.append(f"• 📦 Dealer has {dealer_pending} other pending DNs")
            if dn_health_score < 50:
                recommendations.append("• ⚠️ DN Health Critical - Immediate action required")
            
            if len(recommendations) == 0:
                recommendations.append("• ✅ No action needed")
            
            response += "\n".join(recommendations) + "\n"
            
            return {
                "success": True,
                "dn_number": dn_number,
                "dealer_name": dealer_name,
                "total_units": total_units,
                "total_value": total_value,
                "product_count": len(product_summary),
                "products": list(product_summary.keys()),
                "dn_aging_days": dn_aging_days,
                "dispatch_aging_days": dispatch_aging_days,
                "delivery_aging_days": delivery_aging_days,
                "pod_aging_days": pod_aging_days,
                "pending_pod_aging": pending_pod_aging,
                "dn_health_score": dn_health_score,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "risk_factors": risk_factors,
                "exception_flags": exception_flags,
                "dealer_summary": {
                    "total_dns": dealer_total_dns,
                    "delivered": dealer_delivered,
                    "pending": dealer_pending,
                    "total_value": dealer_total_value,
                    "health_score": dealer_health_score
                },
                "formatted_response": response
            }
            
        except Exception as e:
            logger.error(f"DN intelligence error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    # ==========================================================
    # DN STATUS ENGINE METHODS
    # ==========================================================
    
    def get_pending_dns(self, limit: int = 20) -> List[Dict]:
        """Get all pending DNs (PGI not completed)"""
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.pgi_status != "Completed"
            ).order_by(
                DeliveryReport.dn_create_date
            ).limit(limit).all()
            
            return [{"dn_no": r.dn_no, "dealer": r.customer_name, 
                     "amount": float(r.dn_amount or 0),
                     "created_date": r.dn_create_date} for r in results]
        except Exception as e:
            logger.error(f"Error getting pending DNs: {e}")
            return []
    
    def get_dns_older_than(self, days: int, limit: int = 20) -> List[Dict]:
        """Get DNs older than specified days"""
        try:
            cutoff_date = datetime.now().date() - timedelta(days=days)
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.pgi_status
            ).filter(
                DeliveryReport.dn_create_date <= cutoff_date,
                DeliveryReport.pgi_status != "Completed"
            ).order_by(
                DeliveryReport.dn_create_date
            ).limit(limit).all()
            
            return [{"dn_no": r.dn_no, "dealer": r.customer_name,
                     "amount": float(r.dn_amount or 0),
                     "age_days": days,
                     "status": r.pgi_status} for r in results]
        except Exception as e:
            logger.error(f"Error getting old DNs: {e}")
            return []
    
    def get_delayed_dns(self, delay_threshold_days: int = 7, limit: int = 20) -> List[Dict]:
        """Get DNs with dispatch delay beyond threshold"""
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date
            ).filter(
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.dn_create_date.isnot(None)
            ).all()
            
            delayed = []
            for r in results:
                if isinstance(r.dn_create_date, datetime):
                    create_date = r.dn_create_date.date()
                else:
                    create_date = r.dn_create_date
                if isinstance(r.good_issue_date, datetime):
                    pgi_date = r.good_issue_date.date()
                else:
                    pgi_date = r.good_issue_date
                
                dispatch_days = (pgi_date - create_date).days if pgi_date and create_date else 0
                if dispatch_days > delay_threshold_days:
                    delayed.append({
                        "dn_no": r.dn_no,
                        "dealer": r.customer_name,
                        "amount": float(r.dn_amount or 0),
                        "dispatch_delay_days": dispatch_days
                    })
            
            delayed.sort(key=lambda x: x["dispatch_delay_days"], reverse=True)
            return delayed[:limit]
        except Exception as e:
            logger.error(f"Error getting delayed DNs: {e}")
            return []
    
    # ==========================================================
    # DEALER EXECUTIVE DASHBOARD (Optimized)
    # ==========================================================
    
    def get_dealer_executive_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Optimized dealer dashboard using SQL aggregation"""
        try:
            # Find dealer with exact match first
            exact_match = self.db.query(DeliveryReport).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name.strip())
            ).first()
            
            if not exact_match:
                # Partial match for suggestions
                partial_matches = self.db.query(
                    DeliveryReport.customer_name,
                    func.count(DeliveryReport.dn_no).label("count")
                ).filter(
                    DeliveryReport.customer_name.ilike(f"%{dealer_name.strip()}%")
                ).group_by(
                    DeliveryReport.customer_name
                ).order_by(
                    desc("count")
                ).limit(5).all()
                
                if partial_matches:
                    suggestions = [m.customer_name for m in partial_matches]
                    return {
                        "success": False,
                        "message": f"❌ Dealer '{dealer_name}' not found.\n\nDid you mean:\n" + "\n".join([f"{i+1}. {s}" for i, s in enumerate(suggestions[:5])]),
                        "suggestions": suggestions
                    }
                return {"success": False, "message": f"❌ Dealer '{dealer_name}' not found"}
            
            found_dealer = exact_match.customer_name
            
            # SQL Aggregation for dealer metrics
            result = self.db.query(
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").label("delivered_dns"),
                func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status == "Completed").label("delivered_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns"),
                func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status != "Completed").label("pending_value"),
                func.count(DeliveryReport.dn_no).filter(
                    DeliveryReport.pgi_status == "Completed",
                    DeliveryReport.pod_status == "Pending"
                ).label("pod_pending_dns"),
                func.sum(DeliveryReport.dn_amount).filter(
                    DeliveryReport.pgi_status == "Completed",
                    DeliveryReport.pod_status == "Pending"
                ).label("pod_pending_value")
            ).filter(
                DeliveryReport.customer_name == found_dealer
            ).first()
            
            total_dns = result.total_dns or 0
            delivered = result.delivered_dns or 0
            pending = result.pending_dns or 0
            pod_pending = result.pod_pending_dns or 0
            total_value = float(result.total_value or 0)
            pending_value = float(result.pending_value or 0)
            pod_pending_value = float(result.pod_pending_value or 0)
            
            delivery_rate = (delivered / total_dns) * 100 if total_dns > 0 else 0
            pod_rate = ((delivered - pod_pending) / delivered) * 100 if delivered > 0 else 0
            health_score = (delivery_rate * 0.6) + (pod_rate * 0.4)
            risk_score = 100 - health_score
            
            # Dealer ranking (aggregated)
            all_dealers = self.db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label("total_value")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("total_value")
            ).all()
            
            ranking = 1
            for i, d in enumerate(all_dealers, 1):
                if d.customer_name == found_dealer:
                    ranking = i
                    break
            
            total_all_value = sum(float(d.total_value or 0) for d in all_dealers)
            revenue_contribution = (total_value / total_all_value) * 100 if total_all_value > 0 else 0
            
            # Aging calculation (aggregated)
            aging_result = self.db.query(
                func.avg(func.extract('day', datetime.now() - DeliveryReport.dn_create_date)).label("avg_aging")
            ).filter(
                DeliveryReport.customer_name == found_dealer,
                DeliveryReport.dn_create_date.isnot(None)
            ).first()
            avg_aging = round(float(aging_result.avg_aging or 0), 1)
            
            # Risk level
            if risk_score >= 70:
                risk_level = "CRITICAL"
                risk_icon = "💀"
            elif risk_score >= 50:
                risk_level = "HIGH"
                risk_icon = "🚨"
            elif risk_score >= 30:
                risk_level = "MEDIUM"
                risk_icon = "⚠️"
            else:
                risk_level = "LOW"
                risk_icon = "✅"
            
            response = f"""╔══════════════════════════════════════════════════════════════╗
║              📊 EXECUTIVE DEALER DASHBOARD                       ║
║                    {found_dealer[:30]}                               ║
╚══════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {total_dns:,}
• Delivered: {delivered} ✅
• Pending: {pending} ⏳
• POD Pending: {pod_pending} 📋
• Delivery Rate: {delivery_rate:.1f}%
• POD Compliance: {pod_rate:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIAL ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Value: Rs {total_value:,.2f}
• Pending Value: Rs {pending_value:,.2f}
• POD Pending Value: Rs {pod_pending_value:,.2f}
• Revenue Contribution: {revenue_contribution:.1f}% of total
• Rank: #{ranking} of {len(all_dealers)} dealers

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *RISK & HEALTH ASSESSMENT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{risk_icon} Health Score: {health_score:.1f}/100
• Risk Score: {risk_score:.1f}/100
• Risk Level: {risk_level}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AVERAGE AGING: {avg_aging} days*

💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            recs = []
            if pending > 0:
                recs.append(f"• Clear {pending} pending deliveries")
            if pod_pending > 0:
                recs.append(f"• Collect POD for {pod_pending} delivered DNs")
            if avg_aging > 15:
                recs.append("• Review dispatch process for delays")
            if len(recs) == 0:
                recs.append("• ✅ No action needed")
            
            response += "\n".join(recs)
            
            return {
                "success": True,
                "dealer_name": found_dealer,
                "ranking": ranking,
                "health_score": health_score,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "revenue_contribution": revenue_contribution,
                "avg_aging": avg_aging,
                "formatted_response": response
            }
            
        except Exception as e:
            logger.error(f"Dealer dashboard error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    # ==========================================================
    # POD INTELLIGENCE METHODS
    # ==========================================================
    
    def get_pending_pods(self, limit: int = 20) -> List[Dict]:
        """Get all pending PODs with aging"""
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.delivery_date,
                DeliveryReport.good_issue_date
            ).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).all()
            
            pending_pods = []
            for r in results:
                if r.delivery_date:
                    if isinstance(r.delivery_date, datetime):
                        delivery_date = r.delivery_date.date()
                    else:
                        delivery_date = r.delivery_date
                    aging_days = (datetime.now().date() - delivery_date).days
                elif r.good_issue_date:
                    if isinstance(r.good_issue_date, datetime):
                        pgi_date = r.good_issue_date.date()
                    else:
                        pgi_date = r.good_issue_date
                    aging_days = (datetime.now().date() - pgi_date).days
                else:
                    aging_days = 0
                
                pending_pods.append({
                    "dn_no": r.dn_no,
                    "dealer": r.customer_name,
                    "amount": float(r.dn_amount or 0),
                    "aging_days": aging_days
                })
            
            pending_pods.sort(key=lambda x: x["aging_days"], reverse=True)
            return pending_pods[:limit]
        except Exception as e:
            logger.error(f"Error getting pending PODs: {e}")
            return []
    
    def get_pod_delay_by_dealer(self, limit: int = 20) -> List[Dict]:
        """Get dealers with maximum POD delay"""
        try:
            pending_pods = self.get_pending_pods(100)
            dealer_delays = defaultdict(lambda: {"total_amount": 0, "count": 0, "max_aging": 0})
            
            for pod in pending_pods:
                dealer_delays[pod["dealer"]]["total_amount"] += pod["amount"]
                dealer_delays[pod["dealer"]]["count"] += 1
                dealer_delays[pod["dealer"]]["max_aging"] = max(dealer_delays[pod["dealer"]]["max_aging"], pod["aging_days"])
            
            result = []
            for dealer, data in dealer_delays.items():
                result.append({
                    "dealer": dealer,
                    "pending_pod_count": data["count"],
                    "pending_amount": data["total_amount"],
                    "max_aging_days": data["max_aging"]
                })
            
            result.sort(key=lambda x: x["max_aging_days"], reverse=True)
            return result[:limit]
        except Exception as e:
            logger.error(f"Error getting POD delay by dealer: {e}")
            return []
    
    # ==========================================================
    # PGI INTELLIGENCE METHODS
    # ==========================================================
    
    def get_pending_pgi(self, limit: int = 20) -> List[Dict]:
        """Get all pending PGI DNs"""
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.pgi_status != "Completed"
            ).order_by(
                DeliveryReport.dn_create_date
            ).limit(limit).all()
            
            pending = []
            for r in results:
                if r.dn_create_date:
                    if isinstance(r.dn_create_date, datetime):
                        create_date = r.dn_create_date.date()
                    else:
                        create_date = r.dn_create_date
                    aging = (datetime.now().date() - create_date).days
                else:
                    aging = 0
                
                pending.append({
                    "dn_no": r.dn_no,
                    "dealer": r.customer_name,
                    "amount": float(r.dn_amount or 0),
                    "aging_days": aging
                })
            
            return pending
        except Exception as e:
            logger.error(f"Error getting pending PGI: {e}")
            return []
    
    # ==========================================================
    # NETWORK HEALTH (Optimized with SQL aggregation)
    # ==========================================================
    
    def get_enhanced_network_health(self) -> Dict[str, Any]:
        """Optimized network health using SQL aggregation"""
        try:
            # Single query for all metrics
            result = self.db.query(
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").label("delivered_dns"),
                func.count(DeliveryReport.dn_no).filter(
                    DeliveryReport.pgi_status == "Completed",
                    DeliveryReport.pod_status == "Received"
                ).label("pod_received"),
                func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status != "Completed").label("pending_value"),
                func.avg(func.extract('day', datetime.now() - DeliveryReport.dn_create_date)).filter(
                    DeliveryReport.pgi_status != "Completed"
                ).label("avg_dispatch_aging")
            ).first()
            
            total_dns = result.total_dns or 0
            delivered_dns = result.delivered_dns or 0
            pod_received = result.pod_received or 0
            pending_value = float(result.pending_value or 0)
            avg_dispatch_aging = round(float(result.avg_dispatch_aging or 0), 1)
            
            delivery_rate = (delivered_dns / total_dns) * 100 if total_dns > 0 else 0
            pod_rate = (pod_received / delivered_dns) * 100 if delivered_dns > 0 else 0
            
            # Weighted health score
            delivery_score = delivery_rate * 0.40
            pod_score = pod_rate * 0.30
            aging_score = max(0, 100 - (avg_dispatch_aging * 2)) * 0.30 if avg_dispatch_aging > 0 else 100 * 0.30
            health_score = delivery_score + pod_score + aging_score
            
            return {
                "total_dns": total_dns,
                "delivered_dns": delivered_dns,
                "delivery_rate": round(delivery_rate, 1),
                "pod_rate": round(pod_rate, 1),
                "health_score": round(health_score, 1),
                "revenue_at_risk": pending_value,
                "avg_dispatch_aging": avg_dispatch_aging
            }
        except Exception as e:
            logger.error(f"Network health error: {e}")
            return {}
    
    # ==========================================================
    # SUPPORTING METHODS (Optimized)
    # ==========================================================
    
    def get_top_dealers(self, limit: int = 20) -> List[Dict]:
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("total_value")
            ).limit(limit).all()
            
            return [{"name": r.customer_name, "total_dns": r.total_dns, "total_value": float(r.total_value or 0)} for r in results]
        except Exception as e:
            return []
    
    def get_enhanced_top_risk_dealers(self, limit: int = 20) -> List[Dict]:
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns"),
                func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status != "Completed").label("pending_value")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("pending_value")
            ).limit(limit).all()
            
            dealers = []
            for r in results:
                pending_ratio = (r.pending_dns / r.total_dns) * 100 if r.total_dns > 0 else 0
                risk_score = pending_ratio + ((r.pending_value / (r.total_value or 1)) * 100 if r.total_value else 0) / 2
                dealers.append({
                    "name": r.customer_name,
                    "pending_dns": r.pending_dns or 0,
                    "pending_value": float(r.pending_value or 0),
                    "risk_score": round(risk_score, 1)
                })
            
            return dealers[:limit]
        except Exception as e:
            return []
    
    def get_city_performance(self) -> List[Dict]:
        try:
            results = self.db.query(
                DeliveryReport.ship_to_city,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns")
            ).filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).group_by(
                DeliveryReport.ship_to_city
            ).all()
            
            cities = []
            for r in results:
                pending_rate = (r.pending_dns / r.total_dns) * 100 if r.total_dns > 0 else 0
                status = "🔴" if pending_rate > 30 else "🟡" if pending_rate > 15 else "🟢"
                cities.append({
                    "city": r.ship_to_city,
                    "total_dns": r.total_dns,
                    "pending_dns": r.pending_dns or 0,
                    "pending_rate": round(pending_rate, 1),
                    "total_value": float(r.total_value or 0),
                    "status": status
                })
            
            cities.sort(key=lambda x: x["pending_rate"], reverse=True)
            return cities[:20]
        except Exception as e:
            return []
    
    def get_warehouse_performance(self) -> List[Dict]:
        try:
            results = self.db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns")
            ).filter(
                DeliveryReport.warehouse.isnot(None)
            ).group_by(
                DeliveryReport.warehouse
            ).all()
            
            warehouses = []
            for r in results:
                pending_rate = (r.pending_dns / r.total_dns) * 100 if r.total_dns > 0 else 0
                status = "🔴" if pending_rate > 30 else "🟡" if pending_rate > 15 else "🟢"
                warehouses.append({
                    "warehouse": r.warehouse,
                    "total_dns": r.total_dns,
                    "pending_dns": r.pending_dns or 0,
                    "pending_rate": round(pending_rate, 1),
                    "total_value": float(r.total_value or 0),
                    "status": status
                })
            
            warehouses.sort(key=lambda x: x["pending_rate"], reverse=True)
            return warehouses[:20]
        except Exception as e:
            return []
    
    def get_revenue_analysis(self) -> Dict[str, Any]:
        try:
            total = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
            delivered = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(DeliveryReport.pgi_status == "Completed").scalar() or 0
            pending = total - delivered
            pod_pending = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar() or 0
            
            return {
                "total_revenue": float(total),
                "delivered_revenue": float(delivered),
                "pending_revenue": float(pending),
                "pod_pending_revenue": float(pod_pending),
                "realized_revenue": float(delivered - pod_pending),
                "realization_rate": ((delivered - pod_pending) / total * 100) if total > 0 else 0,
                "revenue_at_risk": float(pending + pod_pending)
            }
        except Exception as e:
            return {}
    
    def get_outstanding_analysis(self) -> Dict[str, Any]:
        revenue = self.get_revenue_analysis()
        return {
            "outstanding_value": revenue.get("pending_revenue", 0) + revenue.get("pod_pending_revenue", 0),
            "pending_delivery": revenue.get("pending_revenue", 0),
            "pod_pending": revenue.get("pod_pending_revenue", 0)
        }
    
    def get_executive_context(self) -> Dict[str, Any]:
        """Get complete executive context for AI"""
        return {
            "network_health": self.get_enhanced_network_health(),
            "top_dealers": self.get_top_dealers(10),
            "top_risk_dealers": self.get_enhanced_top_risk_dealers(10),
            "city_performance": self.get_city_performance()[:5],
            "warehouse_performance": self.get_warehouse_performance()[:5],
            "revenue_analysis": self.get_revenue_analysis(),
            "pending_pods": self.get_pending_pods(5),
            "pending_pgi": self.get_pending_pgi(5),
            "delayed_dns": self.get_delayed_dns(7, 5)
        }


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    
    @staticmethod
    def welcome() -> str:
        return WELCOME_MESSAGE
    
    @staticmethod
    def top_dealers_response(dealers: List, limit: int = 20) -> str:
        if not dealers:
            return "📊 No dealer data available."
        
        response = "🏆 *TOP 20 PERFORMING DEALERS*\n\n"
        for i, d in enumerate(dealers[:limit], 1):
            response += f"{i}. *{d['name'][:35]}*\n"
            response += f"   💰 Rs {d['total_value']:,.2f} | 📦 {d['total_dns']} DNs\n\n"
        return response
    
    @staticmethod
    def top_risk_dealers_response(dealers: List, limit: int = 20) -> str:
        if not dealers:
            return "🚨 No risk data available."
        
        response = "🚨 *TOP 20 RISK DEALERS*\n\n"
        for i, d in enumerate(dealers[:limit], 1):
            response += f"{i}. *{d['name'][:35]}*\n"
            response += f"   📊 Risk Score: {d.get('risk_score', 0)}/100\n"
            response += f"   ⏳ {d.get('pending_dns', 0)} pending\n"
            response += f"   💰 Rs {d.get('pending_value', 0):,.2f} at risk\n\n"
        return response
    
    @staticmethod
    def network_health_response(health: Dict) -> str:
        return f"""📊 *NETWORK HEALTH SCORE*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100
• Total DNs: {health.get('total_dns', 0):,}
• Delivered: {health.get('delivered_dns', 0):,}
• Delivery Rate: {health.get('delivery_rate', 0)}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *POD COMPLIANCE: {health.get('pod_rate', 0)}%*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *REVENUE AT RISK: Rs {health.get('revenue_at_risk', 0):,.2f}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AVG DISPATCH AGING: {health.get('avg_dispatch_aging', 0)} days*

💡 Type "Executive summary" for detailed analysis"""
    
    @staticmethod
    def executive_summary_response(health: Dict, top_dealers: List, risk_dealers: List) -> str:
        response = f"""👑 *EXECUTIVE SUMMARY DASHBOARD*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *NETWORK HEALTH*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100
• Delivery Rate: {health.get('delivery_rate', 0)}%
• POD Compliance: {health.get('pod_rate', 0)}%
• Revenue at Risk: Rs {health.get('revenue_at_risk', 0):,.2f}
• Avg Dispatch Aging: {health.get('avg_dispatch_aging', 0)} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 *TOP 5 DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(top_dealers[:5], 1):
            response += f"{i}. {d['name'][:30]} - Rs {d['total_value']:,.2f}\n"
        
        response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 *TOP 5 RISK DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(risk_dealers[:5], 1):
            response += f"{i}. {d['name'][:30]} - Risk: {d.get('risk_score', 0)}/100 | {d.get('pending_dns', 0)} pending\n"
        
        response += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRIORITY ACTIONS:*
1. Escalate top 5 risk dealers immediately
2. Focus POD collection on pending DNs
3. Review warehouse dispatch process for delays

Type "Help" for all commands"""
        
        return response
    
    @staticmethod
    def pending_dns_response(dns_list: List, title: str = "Pending DNs") -> str:
        if not dns_list:
            return f"✅ No {title.lower()} found."
        
        response = f"📋 *{title}*\n\n"
        for d in dns_list[:15]:
            response += f"🔢 *{d['dn_no']}*\n"
            response += f"   🏪 {d['dealer'][:30]}\n"
            response += f"   💰 Rs {d['amount']:,.2f}\n"
            if d.get('aging_days', 0) > 0:
                response += f"   ⏱️ {d['aging_days']} days old\n"
            response += "\n"
        return response


# ==========================================================
# CONVERSATION MEMORY & CACHE
# ==========================================================

class ConversationMemory:
    def __init__(self, max_history: int = 10):
        self.history: Dict[str, deque] = {}
        self.max_history = max_history
    
    def get_or_create(self, phone_number: str) -> deque:
        if phone_number not in self.history:
            self.history[phone_number] = deque(maxlen=self.max_history)
        return self.history[phone_number]
    
    def add(self, phone_number: str, question: str, response: str, intent: str, entity: str = None):
        memory = self.get_or_create(phone_number)
        memory.append({
            "question": question,
            "response": response[:200],
            "intent": intent,
            "entity": entity,
            "timestamp": datetime.utcnow().isoformat()
        })
    
    def get_last_context(self, phone_number: str) -> Dict[str, Any]:
        memory = self.get_or_create(phone_number)
        if not memory:
            return {}
        last = memory[-1]
        return {
            "last_question": last.get("question"),
            "last_intent": last.get("intent"),
            "last_entity": last.get("entity"),
            "recent_entities": [m.get("entity") for m in list(memory)[-3:] if m.get("entity")]
        }


class ExecutiveContextCache:
    def __init__(self, ttl_seconds: int = 300):
        self.cache: Dict[str, Tuple[Dict, float]] = {}
        self.ttl = ttl_seconds
    
    def get(self, key: str) -> Optional[Dict]:
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return data
            del self.cache[key]
        return None
    
    def set(self, key: str, data: Dict):
        self.cache[key] = (data, time.time())


# ==========================================================
# INTENT TYPES & DETECTION
# ==========================================================

class IntentType(str, Enum):
    HELP = "help"
    WELCOME = "welcome"
    DEALER_LOOKUP = "dealer_lookup"
    DN_LOOKUP = "dn_lookup"
    DN_STATUS = "dn_status"
    DN_PENDING = "dn_pending"
    DN_DELAYED = "dn_delayed"
    DN_OLDER = "dn_older"
    TOP_DEALERS = "top_dealers"
    TOP_RISK_DEALERS = "top_risk_dealers"
    EXECUTIVE_SUMMARY = "executive_summary"
    NETWORK_HEALTH = "network_health"
    CITY_PERFORMANCE = "city_performance"
    WAREHOUSE_PERFORMANCE = "warehouse_performance"
    REVENUE_ANALYSIS = "revenue_analysis"
    OUTSTANDING_ANALYSIS = "outstanding_analysis"
    POD_ANALYSIS = "pod_analysis"
    PENDING_POD = "pending_pod"
    PENDING_PGI = "pending_pgi"
    GENERAL_QUERY = "general_query"


WELCOME_MESSAGE = """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT*

Welcome! I can analyze Dealers, DNs, PODs, Warehouses, Cities, Financial Performance, Risks, and Executive KPIs in real-time.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *What You Can Ask:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏪 *Dealers* - Type a dealer name
🔢 *DN Tracking* - Send a 10-digit DN number
📋 *DN Status* - "Pending DNs", "Delayed DNs"
📋 *POD Status* - "Pending PODs"
👑 *Executive Reports* - "Executive summary"
🏭 *Warehouse* - "Warehouse performance"
🌆 *Cities* - "City performance"
💰 *Financial* - "Revenue analysis"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Examples: "Exact Trading Co", "6243611920", "Executive summary", "Pending DNs"*"""


class IntentDetector:
    
    # Keyword groups for better detection
    DN_KEYWORDS = ["dn", "delivery note", "delivery order"]
    PENDING_KEYWORDS = ["pending", "not delivered", "not pgi", "undelivered", "open"]
    DELAYED_KEYWORDS = ["delay", "delayed", "late", "overdue", "breach"]
    POD_KEYWORDS = ["pod", "proof of delivery", "pod pending"]
    PGI_KEYWORDS = ["pgi", "goods issue", "dispatch"]
    DEALER_KEYWORDS = ["dealer", "customer", "distributor"]
    WAREHOUSE_KEYWORDS = ["warehouse", "godown", "stock point"]
    CITY_KEYWORDS = ["city", "cities", "location"]
    RISK_KEYWORDS = ["risk", "critical", "urgent", "escalate"]
    REVENUE_KEYWORDS = ["revenue", "sales", "amount", "value", "financial"]
    
    @staticmethod
    def detect_dn(message: str) -> Tuple[bool, Optional[str]]:
        match = re.search(r'\b(\d{10})\b', message)
        if match:
            return True, match.group(1)
        return False, None
    
    @staticmethod
    def is_business_question(message: str) -> bool:
        msg_lower = message.lower().strip()
        
        question_words = ["how", "what", "why", "when", "where", "who", "which", "can you", "could you", "please", "tell me"]
        if any(msg_lower.startswith(q) for q in question_words):
            return True
        if msg_lower.endswith("?"):
            return True
        
        logistics_keywords = [
            "analysis", "analyze", "improvement", "recommend", "risk", "trend", "forecast", 
            "delay", "performance", "dealer", "dn", "dispatch", "delivery", "pod", "warehouse", 
            "aging", "revenue", "logistics", "network", "pending", "outstanding"
        ]
        if any(word in msg_lower for word in logistics_keywords):
            return True
        
        return False
    
    @staticmethod
    def detect_intent(message: str) -> Tuple[IntentType, Optional[str]]:
        msg_lower = message.lower().strip()
        msg_original = message.strip()
        
        if IntentDetector.is_business_question(msg_original):
            return IntentType.GENERAL_QUERY, None
        
        if any(word in msg_lower for word in ["help", "menu", "welcome", "hello", "hi", "hey"]):
            return IntentType.HELP, None
        
        is_dn, dn_num = IntentDetector.detect_dn(msg_lower)
        if is_dn:
            return IntentType.DN_LOOKUP, dn_num
        
        # DN Status Queries
        if any(word in msg_lower for word in IntentDetector.PENDING_KEYWORDS) and \
           any(word in msg_lower for word in IntentDetector.DN_KEYWORDS):
            return IntentType.DN_PENDING, None
        
        if any(word in msg_lower for word in IntentDetector.DELAYED_KEYWORDS) and \
           any(word in msg_lower for word in IntentDetector.DN_KEYWORDS):
            return IntentType.DN_DELAYED, None
        
        # POD Queries
        if any(word in msg_lower for word in IntentDetector.POD_KEYWORDS):
            if "pending" in msg_lower or "delay" in msg_lower:
                return IntentType.PENDING_POD, None
            return IntentType.POD_ANALYSIS, None
        
        # PGI Queries
        if any(word in msg_lower for word in IntentDetector.PGI_KEYWORDS) and "pending" in msg_lower:
            return IntentType.PENDING_PGI, None
        
        # Executive Queries
        if any(word in msg_lower for word in ["executive summary", "ceo summary", "management summary"]):
            return IntentType.EXECUTIVE_SUMMARY, None
        
        if any(word in msg_lower for word in ["network health", "health score", "network status"]):
            return IntentType.NETWORK_HEALTH, None
        
        # Risk Queries
        if any(word in msg_lower for word in IntentDetector.RISK_KEYWORDS):
            if "dealer" in msg_lower or "top risk" in msg_lower:
                return IntentType.TOP_RISK_DEALERS, None
        
        # Dealer Queries
        if any(word in msg_lower for word in ["top dealer", "top performing", "best dealer"]):
            return IntentType.TOP_DEALERS, None
        
        # Performance Queries
        if any(word in msg_lower for word in IntentDetector.CITY_KEYWORDS):
            return IntentType.CITY_PERFORMANCE, None
        
        if any(word in msg_lower for word in IntentDetector.WAREHOUSE_KEYWORDS):
            return IntentType.WAREHOUSE_PERFORMANCE, None
        
        # Financial Queries
        if any(word in msg_lower for word in IntentDetector.REVENUE_KEYWORDS):
            if "outstanding" in msg_lower or "pending value" in msg_lower:
                return IntentType.OUTSTANDING_ANALYSIS, None
            return IntentType.REVENUE_ANALYSIS, None
        
        # Default to dealer lookup for short text
        if len(msg_lower.split()) <= 5 and not msg_lower.isdigit():
            return IntentType.DEALER_LOOKUP, msg_original
        
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# MAIN AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    
    def __init__(self, db: Session):
        self.db = db
        self.db_service = DNCentricDatabaseService(db)
        self.formatter = ResponseFormatter()
        self.conversation_memory = ConversationMemory()
        self.executive_cache = ExecutiveContextCache()
        self.db_hash = hashlib.md5(str(db).encode()).hexdigest()
        
        # Initialize AI Provider (GROQ)
        self.ai_provider = None
        self.ai_available = False
        
        if AI_PROVIDER_AVAILABLE:
            try:
                self.ai_provider = get_ai_provider_service(db)
                if self.ai_provider:
                    self.ai_available = self._check_groq_health()
            except Exception as e:
                logger.error(f"Failed to get AI provider: {e}")
                self.ai_available = False
        
        logger.info("=" * 50)
        logger.info("🚀 AI LOGISTICS INTELLIGENCE ASSISTANT v15.0 (DN-CENTRIC)")
        logger.info(f"GROQ Available: {self.ai_available}")
        logger.info("=" * 50)
    
    def _check_groq_health(self) -> bool:
        if not self.ai_provider:
            return False
        try:
            result = self.ai_provider.answer_question(question="Say 'GROQ is working'", user_role="test")
            return result.get("success", False)
        except Exception as e:
            logger.error(f"GROQ health check error: {e}")
            return False
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        start_time = time.time()
        question = question.strip()
        
        logger.info(f"📱 Processing: {question[:100]}")
        
        conversation_context = {}
        if user_phone:
            conversation_context = self.conversation_memory.get_last_context(user_phone)
        
        intent, entity = IntentDetector.detect_intent(question)
        logger.info(f"🎯 Intent: {intent.value}, Entity: {entity}")
        
        try:
            if intent == IntentType.HELP or intent == IntentType.WELCOME:
                result = self._handle_welcome()
            elif intent == IntentType.DEALER_LOOKUP:
                result = self._handle_dealer_lookup(entity)
            elif intent == IntentType.DN_LOOKUP:
                result = self._handle_dn_lookup(entity)
            elif intent == IntentType.DN_PENDING:
                result = self._handle_pending_dns()
            elif intent == IntentType.DN_DELAYED:
                result = self._handle_delayed_dns()
            elif intent == IntentType.TOP_DEALERS:
                result = self._handle_top_dealers()
            elif intent == IntentType.TOP_RISK_DEALERS:
                result = self._handle_top_risk_dealers()
            elif intent == IntentType.EXECUTIVE_SUMMARY:
                result = self._handle_executive_summary()
            elif intent == IntentType.NETWORK_HEALTH:
                result = self._handle_network_health()
            elif intent == IntentType.CITY_PERFORMANCE:
                result = self._handle_city_performance()
            elif intent == IntentType.WAREHOUSE_PERFORMANCE:
                result = self._handle_warehouse_performance()
            elif intent == IntentType.REVENUE_ANALYSIS:
                result = self._handle_revenue_analysis()
            elif intent == IntentType.OUTSTANDING_ANALYSIS:
                result = self._handle_outstanding_analysis()
            elif intent == IntentType.PENDING_POD:
                result = self._handle_pending_pods()
            elif intent == IntentType.PENDING_PGI:
                result = self._handle_pending_pgi()
            else:
                result = self._handle_general_query(question, user_phone, user_role, conversation_context)
            
            if user_phone and result.get("success"):
                self.conversation_memory.add(user_phone, question, result.get("response", ""), intent.value, entity)
            
            result["processing_time_ms"] = int((time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            logger.error(f"Processing error: {e}")
            return {"success": False, "response": "⚠️ Service unavailable. Please try again.", "processing_time_ms": int((time.time() - start_time) * 1000)}
    
    def _handle_welcome(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.welcome()}
    
    def _handle_dealer_lookup(self, dealer_name: str) -> Dict[str, Any]:
        result = self.db_service.get_dealer_executive_dashboard(dealer_name)
        return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "Dealer not found"))}
    
    def _handle_dn_lookup(self, dn_number: str) -> Dict[str, Any]:
        """DN-Centric: Returns aggregated DN intelligence with all products"""
        result = self.db_service.get_dn_complete_intelligence(dn_number)
        return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "DN not found"))}
    
    def _handle_pending_dns(self) -> Dict[str, Any]:
        pending = self.db_service.get_pending_dns(20)
        return {"success": True, "response": self.formatter.pending_dns_response(pending, "Pending DNs")}
    
    def _handle_delayed_dns(self) -> Dict[str, Any]:
        delayed = self.db_service.get_delayed_dns(7, 20)
        if not delayed:
            return {"success": True, "response": "✅ No delayed DNs found (threshold: 7 days)"}
        
        response = "🚨 *DELAYED DNs (>7 days)*\n\n"
        for d in delayed[:15]:
            response += f"🔢 *{d['dn_no']}*\n"
            response += f"   🏪 {d['dealer'][:30]}\n"
            response += f"   💰 Rs {d['amount']:,.2f}\n"
            response += f"   ⏱️ {d['dispatch_delay_days']} days delayed\n\n"
        return {"success": True, "response": response}
    
    def _handle_pending_pods(self) -> Dict[str, Any]:
        pending_pods = self.db_service.get_pending_pods(20)
        if not pending_pods:
            return {"success": True, "response": "✅ No pending PODs found."}
        
        response = "📋 *PENDING PODs*\n\n"
        for p in pending_pods[:15]:
            response += f"🔢 *{p['dn_no']}*\n"
            response += f"   🏪 {p['dealer'][:30]}\n"
            response += f"   💰 Rs {p['amount']:,.2f}\n"
            response += f"   ⏱️ {p['aging_days']} days pending\n\n"
        return {"success": True, "response": response}
    
    def _handle_pending_pgi(self) -> Dict[str, Any]:
        pending_pgi = self.db_service.get_pending_pgi(20)
        return {"success": True, "response": self.formatter.pending_dns_response(pending_pgi, "Pending PGI DNs")}
    
    def _handle_top_dealers(self) -> Dict[str, Any]:
        dealers = self.db_service.get_top_dealers(20)
        return {"success": True, "response": self.formatter.top_dealers_response(dealers)}
    
    def _handle_top_risk_dealers(self) -> Dict[str, Any]:
        dealers = self.db_service.get_enhanced_top_risk_dealers(20)
        return {"success": True, "response": self.formatter.top_risk_dealers_response(dealers)}
    
    def _handle_executive_summary(self) -> Dict[str, Any]:
        health = self.db_service.get_enhanced_network_health()
        top_dealers = self.db_service.get_top_dealers(10)
        risk_dealers = self.db_service.get_enhanced_top_risk_dealers(10)
        return {"success": True, "response": self.formatter.executive_summary_response(health, top_dealers, risk_dealers)}
    
    def _handle_network_health(self) -> Dict[str, Any]:
        health = self.db_service.get_enhanced_network_health()
        return {"success": True, "response": self.formatter.network_health_response(health)}
    
    def _handle_city_performance(self) -> Dict[str, Any]:
        cities = self.db_service.get_city_performance()
        if not cities:
            return {"success": True, "response": "🌆 No city data available."}
        
        response = "🌆 *CITY PERFORMANCE*\n\n"
        for c in cities[:15]:
            response += f"{c['status']} *{c['city'][:25]}*\n"
            response += f"   📦 {c['total_dns']} DNs | ⏳ {c['pending_dns']} pending ({c['pending_rate']:.0f}%)\n"
            response += f"   💰 Rs {c['total_value']:,.2f}\n\n"
        return {"success": True, "response": response}
    
    def _handle_warehouse_performance(self) -> Dict[str, Any]:
        warehouses = self.db_service.get_warehouse_performance()
        if not warehouses:
            return {"success": True, "response": "🏭 No warehouse data available."}
        
        response = "🏭 *WAREHOUSE PERFORMANCE*\n\n"
        for w in warehouses[:15]:
            response += f"{w['status']} *{w['warehouse'][:25]}*\n"
            response += f"   📦 {w['total_dns']} DNs | ⏳ {w['pending_dns']} pending ({w['pending_rate']:.0f}%)\n"
            response += f"   💰 Rs {w['total_value']:,.2f}\n\n"
        return {"success": True, "response": response}
    
    def _handle_revenue_analysis(self) -> Dict[str, Any]:
        revenue = self.db_service.get_revenue_analysis()
        response = f"""💰 *REVENUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Revenue: Rs {revenue.get('total_revenue', 0):,.2f}
• Realized: Rs {revenue.get('realized_revenue', 0):,.2f} ✅
• Pending Delivery: Rs {revenue.get('pending_revenue', 0):,.2f} ⏳
• POD Pending: Rs {revenue.get('pod_pending_revenue', 0):,.2f} 📋

📈 *REALIZATION RATE: {revenue.get('realization_rate', 0):.1f}%*"""
        return {"success": True, "response": response}
    
    def _handle_outstanding_analysis(self) -> Dict[str, Any]:
        outstanding = self.db_service.get_outstanding_analysis()
        response = f"""💰 *OUTSTANDING & PENDING VALUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *VALUE BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Outstanding: Rs {outstanding.get('outstanding_value', 0):,.2f}
• Pending Delivery: Rs {outstanding.get('pending_delivery', 0):,.2f} ⏳
• POD Pending: Rs {outstanding.get('pod_pending', 0):,.2f} 📋"""
        return {"success": True, "response": response}
    
    def _handle_general_query(self, question: str, user_phone: str, user_role: str, conversation_context: Dict = None) -> Dict[str, Any]:
        logger.info(f"🤖 Processing general query with GROQ: {question[:100]}")
        
        executive_context = self.executive_cache.get(self.db_hash)
        if not executive_context:
            executive_context = self.db_service.get_executive_context()
            self.executive_cache.set(self.db_hash, executive_context)
        
        context_prompt = f"""
BUSINESS CONTEXT:
- Network Health Score: {executive_context.get('network_health', {}).get('health_score', 0)}/100
- Revenue at Risk: Rs {executive_context.get('network_health', {}).get('revenue_at_risk', 0):,.2f}
- Avg Dispatch Aging: {executive_context.get('network_health', {}).get('avg_dispatch_aging', 0)} days
- Top Dealers: {executive_context.get('top_dealers', [])[:3]}
- Top Risk Dealers: {executive_context.get('top_risk_dealers', [])[:3]}
- Pending PODs: {len(executive_context.get('pending_pods', []))}
- Delayed DNs: {len(executive_context.get('delayed_dns', []))}
"""
        
        if conversation_context:
            context_prompt += f"""
CONVERSATION CONTEXT:
- Last Question: {conversation_context.get('last_question', 'None')}
- Last Entity: {conversation_context.get('last_entity', 'None')}
"""
        
        try:
            from app.services.ai_provider_service import get_ai_provider_service
            ai_provider = get_ai_provider_service(self.db)
            
            if ai_provider and ai_provider.is_available:
                result = ai_provider.answer_question(
                    question=f"{context_prompt}\n\nUSER QUESTION: {question}\n\nProvide a helpful, data-driven response for WhatsApp.",
                    user_phone=user_phone,
                    user_role=user_role or "guest"
                )
                
                if result.get("success"):
                    return {"success": True, "response": result.get("content")}
                else:
                    return self._get_fallback_response(question, result.get('error'))
            else:
                return self._get_fallback_response(question, "AI Provider not available")
                
        except Exception as e:
            logger.exception(f"GROQ error: {e}")
            return self._get_fallback_response(question, str(e))
    
    def _get_fallback_response(self, question: str, error: str = None) -> Dict[str, Any]:
        error_msg = f"\n\n*Error Details:* {error[:200]}" if error else ""
        
        response = f"""🤖 *AI LOGISTICS ASSISTANT*

I understand you're asking about: "{question[:50]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *AI Service Unavailable*{error_msg}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Type a dealer name (e.g., "Exact Trading Co")
🔢 Send a 10-digit DN number
👑 "Executive summary" - Leadership view
🏆 "Top dealers" - Best performers
🚨 "Top risk dealers" - Critical accounts
📋 "Pending PODs" - POD collection required
🚚 "Pending PGI" - Dispatch pending

Type "Help" for complete menu."""
        
        return {"success": True, "response": response}


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def process_whatsapp_query(question: str, db: Session, user_phone: str = None, user_role: str = None) -> str:
    """Process WhatsApp query and return response"""
    try:
        service = AIQueryService(db)
        result = service.process_query(question, user_phone, user_role)
        return result.get("response", "Unable to process your request. Please try again.")
    except Exception as e:
        logger.error(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."
