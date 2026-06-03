# ==========================================================
# FILE: app/services/logistics_query_service.py
# ==========================================================
# COMPLETE UPDATED VERSION WITH PRIORITY 1, 2, AND 3 IMPROVEMENTS
# ==========================================================

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, distinct
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, date, timedelta
import re

from app.models import DeliveryReport


class LogisticsQueryService:

    # ======================================================
    # HELPER FUNCTIONS
    # ======================================================
    
    @staticmethod
    def _count_unique_dns(records) -> int:
        """Count unique DN numbers from a list of records."""
        return len(set(str(r.dn_no) for r in records if r.dn_no))
    
    @staticmethod
    def _get_unique_dns(records) -> set:
        """Get unique DN numbers from a list of records."""
        return set(str(r.dn_no) for r in records if r.dn_no)
    
    @staticmethod
    def _get_dn_creation_date(record):
        """Get DN creation date safely."""
        if not record.dn_create_date:
            return None
        if isinstance(record.dn_create_date, datetime):
            return record.dn_create_date.date()
        return record.dn_create_date
    
    @staticmethod
    def _normalize_question(question: str) -> str:
        """Normalize question for better intent detection."""
        normalized = question.lower().strip()
        corrections = {
            "pendinng": "pending",
            "pendding": "pending",
            "pendig": "pending",
            "higest": "highest",
            "highet": "highest",
            "whareouse": "warehouse",
            "wharehouse": "warehouse",
            "warehous": "warehouse",
            "delievry": "delivery",
            "deliverd": "delivered",
            "acknowlegement": "acknowledgement",
            "acknowldgement": "acknowledgement",
            "quantiy": "quantity",
            "qty": "quantity",
            "aging": "ageing",
            "ageing": "aging"
        }
        for wrong, correct in corrections.items():
            normalized = normalized.replace(wrong, correct)
        return normalized
    
    @staticmethod
    def _record_to_dict(record) -> Dict[str, Any]:
        """Convert SQLAlchemy record to dictionary for consistent access."""
        return {
            "id": record.id,
            "dn_no": record.dn_no,
            "order_type": record.order_type,
            "dn_amount": float(record.dn_amount) if record.dn_amount else 0,
            "dn_qty": float(record.dn_qty) if record.dn_qty else 0,
            "dn_work": record.dn_work,
            "division": record.division,
            "material_no": record.material_no,
            "customer_model": record.customer_model,
            "sales_office": record.sales_office,
            "customer_name": record.customer_name,
            "dealer_code": record.dealer_code,
            "ship_to_city": record.ship_to_city,
            "storage_location": record.storage_location,
            "warehouse": record.warehouse,
            "delivery_location": record.delivery_location,
            "dn_create_date": record.dn_create_date.isoformat() if record.dn_create_date else None,
            "good_issue_date": record.good_issue_date.isoformat() if record.good_issue_date else None,
            "pod_date": record.pod_date.isoformat() if record.pod_date else None,
            "delivery_status": record.delivery_status,
            "pgi_status": record.pgi_status,
            "pod_status": record.pod_status,
            "pending_flag": record.pending_flag,
            "sales_manager": record.sales_manager,
            "source_file": record.source_file,
            "upload_batch_id": record.upload_batch_id,
            "imported_at": record.imported_at.isoformat() if record.imported_at else None,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }
    
    # ======================================================
    # CORRECTED AGING CALCULATIONS (Priority 1 - Critical Fix)
    # ======================================================
    
    @staticmethod
    def calculate_dispatch_age(record) -> int:
        """
        Calculate dispatch age = PGI Date - DN Create Date
        Returns days taken from DN creation to dispatch
        """
        if not record.dn_create_date or not record.good_issue_date:
            return 0
        
        create_date = record.dn_create_date.date() if isinstance(record.dn_create_date, datetime) else record.dn_create_date
        issue_date = record.good_issue_date.date() if isinstance(record.good_issue_date, datetime) else record.good_issue_date
        
        return (issue_date - create_date).days
    
    @staticmethod
    def calculate_pod_age(record) -> int:
        """
        Calculate POD age = Today - PGI Date
        Returns days since dispatch without acknowledgement
        """
        if not record.good_issue_date:
            return 0
        
        issue_date = record.good_issue_date.date() if isinstance(record.good_issue_date, datetime) else record.good_issue_date
        
        return (datetime.now().date() - issue_date).days
    
    @staticmethod
    def calculate_delivery_cycle(record) -> int:
        """
        Calculate total delivery cycle = POD Date - DN Create Date
        Returns total days from creation to acknowledgement
        """
        if not record.dn_create_date or not record.pod_date:
            return 0
        
        create_date = record.dn_create_date.date() if isinstance(record.dn_create_date, datetime) else record.dn_create_date
        pod_date = record.pod_date.date() if isinstance(record.pod_date, datetime) else record.pod_date
        
        return (pod_date - create_date).days
    
    @staticmethod
    def get_dn_aging_details(record) -> Dict[str, int]:
        """Get complete aging details for a DN record"""
        return {
            "dispatch_age": LogisticsQueryService.calculate_dispatch_age(record),
            "pod_age": LogisticsQueryService.calculate_pod_age(record),
            "delivery_cycle": LogisticsQueryService.calculate_delivery_cycle(record)
        }
    
    # ======================================================
    # MASTER DEALER DASHBOARD BUILDER (Priority 1 & 2 - Enhanced)
    # ======================================================
    
    @staticmethod
    def get_dealer_complete_dashboard(db: Session, dealer_name: str, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        """
        Master function that builds a complete dealer dashboard in one pass.
        Features corrected aging calculations, enhanced DN details, and pod_pending_amount.
        
        Parameters:
        - dealer_name: Name of the dealer (supports partial/fuzzy matching)
        - page: Page number for paginated results (1-indexed)
        - page_size: Number of pending DNs to show per page
        """
        
        # Step 1: Get all records for this dealer in ONE database query
        records = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
            .all()
        )
        
        if not records:
            # Try fuzzy search as fallback
            fuzzy_result = LogisticsQueryService.search_dealer_fuzzy(db, dealer_name, 5)
            if fuzzy_result.get("success"):
                return {
                    "success": True,
                    "fuzzy": True,
                    "matches": fuzzy_result["matches"],
                    "summary": f"Multiple dealers found matching '{dealer_name}'. Please select one:\n\n" + 
                               "\n".join([f"{i+1}. {m['dealer_name']} - {m['total_dns']} DNs" 
                                         for i, m in enumerate(fuzzy_result["matches"][:5])])
                }
            return {"success": False, "message": f"No dealer found matching '{dealer_name}'"}
        
        actual_dealer_name = records[0].customer_name if records else dealer_name
        
        # Step 2: Group by unique DN with enhanced fields (Priority 2)
        dn_groups = {}
        product_summary = {}
        warehouse_stats = {}
        city_stats = {}
        
        for r in records:
            dn_no = str(r.dn_no)
            
            # Group by DN
            if dn_no not in dn_groups:
                # Determine status using business rules
                if r.pgi_status == "Completed" and r.pod_status == "Received":
                    status = "delivered_acknowledged"
                    status_text = "Delivered and Acknowledged"
                elif r.pgi_status == "Completed" and r.pod_status == "Pending":
                    status = "delivered_not_acknowledged"
                    status_text = "Delivered, Awaiting Acknowledgement"
                elif r.pgi_status == "Pending":
                    status = "pending"
                    status_text = "Pending Dispatch"
                else:
                    status = "unknown"
                    status_text = r.delivery_status or "Unknown"
                
                # Get corrected aging details (Priority 1)
                aging = LogisticsQueryService.get_dn_aging_details(r)
                
                # Format dates for display
                dn_create_date_str = ""
                if r.dn_create_date:
                    if isinstance(r.dn_create_date, datetime):
                        dn_create_date_str = r.dn_create_date.strftime('%d-%b')
                    else:
                        dn_create_date_str = r.dn_create_date.strftime('%d-%b') if hasattr(r.dn_create_date, 'strftime') else str(r.dn_create_date)
                
                good_issue_date_str = ""
                if r.good_issue_date:
                    if isinstance(r.good_issue_date, datetime):
                        good_issue_date_str = r.good_issue_date.strftime('%d-%b')
                    else:
                        good_issue_date_str = r.good_issue_date.strftime('%d-%b') if hasattr(r.good_issue_date, 'strftime') else str(r.good_issue_date)
                
                pod_date_str = ""
                if r.pod_date:
                    if isinstance(r.pod_date, datetime):
                        pod_date_str = r.pod_date.strftime('%d-%b')
                    else:
                        pod_date_str = r.pod_date.strftime('%d-%b') if hasattr(r.pod_date, 'strftime') else str(r.pod_date)
                
                dn_groups[dn_no] = {
                    "dn_no": dn_no,
                    "status": status,
                    "status_text": status_text,
                    "pod_status": "Received" if r.pod_status == "Received" else "Pending",
                    "total_quantity": 0,
                    "total_amount": 0,
                    "dn_create_date": r.dn_create_date,
                    "dn_create_date_str": dn_create_date_str,
                    "good_issue_date": r.good_issue_date,
                    "good_issue_date_str": good_issue_date_str,
                    "pod_date": r.pod_date,
                    "pod_date_str": pod_date_str,
                    "dispatch_age": aging["dispatch_age"],
                    "pod_age": aging["pod_age"],
                    "delivery_cycle": aging["delivery_cycle"],
                    "warehouse": r.warehouse,
                    "city": r.ship_to_city,
                    "products": []
                }
            
            # Add product to DN group
            product = {
                "material_no": r.material_no,
                "product_name": r.customer_model or r.material_no,
                "quantity": float(r.dn_qty or 0),
                "amount": float(r.dn_amount or 0)
            }
            dn_groups[dn_no]["products"].append(product)
            dn_groups[dn_no]["total_quantity"] += product["quantity"]
            dn_groups[dn_no]["total_amount"] += product["amount"]
            
            # Build product summary (across all DNs)
            product_key = r.customer_model or r.material_no
            if product_key not in product_summary:
                product_summary[product_key] = {
                    "product_name": product_key,
                    "total_quantity": 0,
                    "delivered_quantity": 0,
                    "pending_quantity": 0,
                    "pod_pending_quantity": 0,
                    "dn_count": set(),
                    "material_no": r.material_no
                }
            
            product_summary[product_key]["total_quantity"] += float(r.dn_qty or 0)
            product_summary[product_key]["dn_count"].add(dn_no)
            
            if r.pgi_status == "Completed":
                product_summary[product_key]["delivered_quantity"] += float(r.dn_qty or 0)
            if r.pgi_status != "Completed":
                product_summary[product_key]["pending_quantity"] += float(r.dn_qty or 0)
            if r.pgi_status == "Completed" and r.pod_status == "Pending":
                product_summary[product_key]["pod_pending_quantity"] += float(r.dn_qty or 0)
            
            # Warehouse stats (for pending only)
            if r.pgi_status != "Completed" and r.warehouse:
                if r.warehouse not in warehouse_stats:
                    warehouse_stats[r.warehouse] = {"dns": set(), "quantity": 0, "amount": 0}
                warehouse_stats[r.warehouse]["dns"].add(dn_no)
                warehouse_stats[r.warehouse]["quantity"] += float(r.dn_qty or 0)
                warehouse_stats[r.warehouse]["amount"] += float(r.dn_amount or 0)
            
            # City stats (for pending only)
            if r.pgi_status != "Completed" and r.ship_to_city:
                if r.ship_to_city not in city_stats:
                    city_stats[r.ship_to_city] = {"dns": set(), "quantity": 0, "amount": 0}
                city_stats[r.ship_to_city]["dns"].add(dn_no)
                city_stats[r.ship_to_city]["quantity"] += float(r.dn_qty or 0)
                city_stats[r.ship_to_city]["amount"] += float(r.dn_amount or 0)
        
        # Step 3: Categorize DNs by status
        delivered_dns = [dn for dn in dn_groups.values() if dn["status"] == "delivered_acknowledged"]
        pending_dns = [dn for dn in dn_groups.values() if dn["status"] == "pending"]
        pod_pending_dns = [dn for dn in dn_groups.values() if dn["status"] == "delivered_not_acknowledged"]
        
        # Sort pending DNs by dispatch_age (older first), POD pending by pod_age
        pending_dns_sorted = sorted(pending_dns, key=lambda x: x["dispatch_age"], reverse=True)
        pod_pending_dns_sorted = sorted(pod_pending_dns, key=lambda x: x["pod_age"], reverse=True)
        
        # Step 4: Calculate KPIs with pod_pending_amount (Priority 1)
        total_dns = len(dn_groups)
        total_units = sum(dn["total_quantity"] for dn in dn_groups.values())
        delivered_units = sum(dn["total_quantity"] for dn in delivered_dns)
        pending_units = sum(dn["total_quantity"] for dn in pending_dns)
        pod_pending_units = sum(dn["total_quantity"] for dn in pod_pending_dns)
        
        total_amount = sum(dn["total_amount"] for dn in dn_groups.values())
        pending_amount = sum(dn["total_amount"] for dn in pending_dns)
        pod_pending_amount = sum(dn["total_amount"] for dn in pod_pending_dns)
        outstanding_amount = pending_amount + pod_pending_amount
        
        # Step 5: Find oldest pending DN
        oldest_pending = pending_dns_sorted[0] if pending_dns_sorted else None
        oldest_pod_pending = pod_pending_dns_sorted[0] if pod_pending_dns_sorted else None
        
        # Step 6: Fix Highest Pending Product Bug (Priority 1)
        # Filter products with pending_quantity > 0 before finding max
        pending_products = [p for p in product_summary.values() if p["pending_quantity"] > 0]
        if pending_products:
            highest_pending_product = max(pending_products, key=lambda x: x["pending_quantity"])
            highest_pending_product_info = {
                "product_name": highest_pending_product["product_name"],
                "pending_quantity": round(highest_pending_product["pending_quantity"], 0)
            }
        else:
            highest_pending_product_info = {"product_name": "None", "pending_quantity": 0}
        
        # Step 7: Paginate pending DNs
        total_pages = (len(pending_dns_sorted) + page_size - 1) // page_size if pending_dns_sorted else 1
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_pending_dns = pending_dns_sorted[start_idx:end_idx]
        
        # Step 8: Build the complete dashboard response
        dashboard = {
            "success": True,
            "dealer_name": actual_dealer_name,
            "kpis": {
                "total_dns": total_dns,
                "total_units": round(total_units, 0),
                "total_amount": round(total_amount, 2),
                "delivered_dns": len(delivered_dns),
                "delivered_units": round(delivered_units, 0),
                "pending_dns": len(pending_dns),
                "pending_units": round(pending_units, 0),
                "pending_amount": round(pending_amount, 2),
                "pod_pending_dns": len(pod_pending_dns),
                "pod_pending_units": round(pod_pending_units, 0),
                "pod_pending_amount": round(pod_pending_amount, 2),
                "outstanding_amount": round(outstanding_amount, 2)
            },
            "alerts": {
                "oldest_pending_dn": {
                    "dn_no": oldest_pending["dn_no"] if oldest_pending else None,
                    "dispatch_age": oldest_pending["dispatch_age"] if oldest_pending else 0,
                    "quantity": oldest_pending["total_quantity"] if oldest_pending else 0,
                    "amount": oldest_pending["total_amount"] if oldest_pending else 0
                } if oldest_pending else None,
                "oldest_pod_pending_dn": {
                    "dn_no": oldest_pod_pending["dn_no"] if oldest_pod_pending else None,
                    "pod_age": oldest_pod_pending["pod_age"] if oldest_pod_pending else 0,
                    "quantity": oldest_pod_pending["total_quantity"] if oldest_pod_pending else 0,
                    "amount": oldest_pod_pending["total_amount"] if oldest_pod_pending else 0
                } if oldest_pod_pending else None,
                "highest_pending_product": highest_pending_product_info
            },
            "pending_dns": paginated_pending_dns,
            "pod_pending_dns": pod_pending_dns_sorted[:10],
            "product_summary": sorted(
                [{
                    "product_name": p["product_name"],
                    "total_quantity": round(p["total_quantity"], 0),
                    "delivered_quantity": round(p["delivered_quantity"], 0),
                    "pending_quantity": round(p["pending_quantity"], 0),
                    "pod_pending_quantity": round(p["pod_pending_quantity"], 0),
                    "dn_count": len(p["dn_count"])
                } for p in product_summary.values()],
                key=lambda x: x["pending_quantity"],
                reverse=True
            )[:15],
            "warehouse_breakdown": sorted(
                [{"warehouse": w, "pending_dns": len(stats["dns"]), "pending_units": round(stats["quantity"], 0), "pending_amount": round(stats["amount"], 2)}
                 for w, stats in warehouse_stats.items()],
                key=lambda x: x["pending_dns"],
                reverse=True
            )[:10],
            "city_breakdown": sorted(
                [{"city": c, "pending_dns": len(stats["dns"]), "pending_units": round(stats["quantity"], 0), "pending_amount": round(stats["amount"], 2)}
                 for c, stats in city_stats.items()],
                key=lambda x: x["pending_dns"],
                reverse=True
            )[:10],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "total_pending_dns": len(pending_dns_sorted),
                "has_next": page < total_pages,
                "has_previous": page > 1
            }
        }
        
        # Step 9: Generate formatted WhatsApp message (Enhanced with Priority 2 format)
        dashboard["formatted_message"] = LogisticsQueryService._format_dealer_dashboard_enhanced(dashboard)
        
        # Step 10: Add AI Insights (Priority 8)
        dashboard["ai_insights"] = LogisticsQueryService._generate_ai_insights_for_dealer(dashboard)
        
        return dashboard
    
    @staticmethod
    def _format_dealer_dashboard_enhanced(dashboard: Dict[str, Any]) -> str:
        """
        Format the dealer dashboard with enhanced DN details.
        Priority 2: Shows DN with Products, Qty, Value, PGI Date, POD Status, Aging
        """
        if dashboard.get("fuzzy"):
            return dashboard.get("summary", "Multiple dealers found")
        
        if not dashboard.get("success"):
            return dashboard.get("message", "Dealer not found")
        
        kpis = dashboard["kpis"]
        alerts = dashboard["alerts"]
        pending_dns = dashboard.get("pending_dns", [])
        pod_pending_dns = dashboard.get("pod_pending_dns", [])
        product_summary = dashboard.get("product_summary", [])
        warehouse_breakdown = dashboard.get("warehouse_breakdown", [])
        city_breakdown = dashboard.get("city_breakdown", [])
        pagination = dashboard.get("pagination", {})
        
        # Build the message
        message = f"📊 *DEALER DASHBOARD: {dashboard['dealer_name']}*\n\n"
        
        # KPIs Section
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += "📈 *KEY PERFORMANCE INDICATORS*\n"
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += f"• Total DNs: *{kpis['total_dns']}*\n"
        message += f"• Total Units: *{kpis['total_units']:,.0f}*\n"
        message += f"• Total Value: *Rs {kpis['total_amount']:,.2f}*\n\n"
        
        message += f"✅ *Delivered:* {kpis['delivered_dns']} DNs ({kpis['delivered_units']:,.0f} units)\n"
        message += f"⏳ *Pending Dispatch:* {kpis['pending_dns']} DNs ({kpis['pending_units']:,.0f} units)\n"
        message += f"   Value: Rs {kpis['pending_amount']:,.2f}\n"
        message += f"📋 *Pending POD:* {kpis['pod_pending_dns']} DNs ({kpis['pod_pending_units']:,.0f} units)\n"
        message += f"   Value: Rs {kpis['pod_pending_amount']:,.2f}\n"
        message += f"💰 *Total Outstanding:* Rs {kpis['outstanding_amount']:,.2f}\n\n"
        
        # Alerts Section (Critical)
        if alerts["oldest_pending_dn"] and alerts["oldest_pending_dn"]["dn_no"]:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "🚨 *CRITICAL ALERTS*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += f"⚠️ *Oldest Pending DN:* {alerts['oldest_pending_dn']['dn_no']}\n"
            message += f"   Dispatch Age: {alerts['oldest_pending_dn']['dispatch_age']} days\n"
            message += f"   Quantity: {alerts['oldest_pending_dn']['quantity']:,.0f} units\n"
            message += f"   Value: Rs {alerts['oldest_pending_dn']['amount']:,.2f}\n\n"
        
        if alerts["oldest_pod_pending_dn"] and alerts["oldest_pod_pending_dn"]["dn_no"]:
            message += f"⚠️ *Oldest POD Pending:* {alerts['oldest_pod_pending_dn']['dn_no']}\n"
            message += f"   POD Age: {alerts['oldest_pod_pending_dn']['pod_age']} days\n"
            message += f"   Quantity: {alerts['oldest_pod_pending_dn']['quantity']:,.0f} units\n"
            message += f"   Value: Rs {alerts['oldest_pod_pending_dn']['amount']:,.2f}\n\n"
        
        if alerts["highest_pending_product"]["product_name"] != "None":
            message += f"⚠️ *Highest Pending Product:* {alerts['highest_pending_product']['product_name']}\n"
            message += f"   Pending Quantity: {alerts['highest_pending_product']['pending_quantity']:,.0f} units\n\n"
        
        # Enhanced Pending DNs Section with full details (Priority 2 format)
        if pending_dns:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += f"⏳ *PENDING DNs (Page {pagination.get('page', 1)} of {pagination.get('total_pages', 1)})*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for dn in pending_dns[:10]:
                # Format using enhanced DN display (Priority 2)
                message += f"🔹 *DN: {dn['dn_no']}*\n"
                message += f"\n"
                message += f"   📅 DN Date: {dn.get('dn_create_date_str', 'N/A')}\n"
                message += f"   🚚 PGI Date: {dn.get('good_issue_date_str', 'Not Dispatched')}\n"
                message += f"\n"
                message += f"   ⏱️ Dispatch Age: {dn['dispatch_age']} days\n"
                message += f"   ⏱️ POD Age: {dn['pod_age']} days\n"
                message += f"\n"
                message += f"   📦 Qty: {dn['total_quantity']:,.0f} units\n"
                message += f"   💰 Value: Rs {dn['total_amount']:,.2f}\n"
                message += f"\n"
                message += f"   📋 Products:\n"
                # Show products with quantities
                for p in dn['products'][:5]:
                    message += f"      • {p['product_name']} = {p['quantity']:,.0f} units\n"
                if len(dn['products']) > 5:
                    message += f"      • +{len(dn['products']) - 5} more products\n"
                message += "\n"
            
            if pagination.get("has_next", False):
                remaining = pagination.get('total_pending_dns', 0) - 10
                message += f"📌 *{remaining} more pending DNs*\n"
                message += f"   Reply: *MORE PENDING* for next page\n\n"
        
        # POD Pending DNs (Enhanced with details)
        if pod_pending_dns:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "📋 *AWAITING ACKNOWLEDGEMENT (POD Pending)*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for dn in pod_pending_dns[:5]:
                message += f"🔹 *DN: {dn['dn_no']}*\n"
                message += f"   PGI Date: {dn.get('good_issue_date_str', 'N/A')}\n"
                message += f"   POD Age: {dn['pod_age']} days\n"
                message += f"   Qty: {dn['total_quantity']:,.0f} units\n"
                message += f"   Value: Rs {dn['total_amount']:,.2f}\n\n"
            if len(pod_pending_dns) > 5:
                message += f"📌 +{len(pod_pending_dns) - 5} more POD pending DNs\n\n"
        
        # Product Summary (Top 10)
        if product_summary:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "📦 *PRODUCT SUMMARY*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for p in product_summary[:10]:
                message += f"• *{p['product_name']}*\n"
                message += f"   Total: {p['total_quantity']:,.0f} | Delivered: {p['delivered_quantity']:,.0f} | Pending: {p['pending_quantity']:,.0f}\n"
            if len(product_summary) > 10:
                message += f"\n📌 +{len(product_summary) - 10} more products\n"
            message += "\n"
        
        # Warehouse Breakdown
        if warehouse_breakdown:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "🏭 *PENDING BY WAREHOUSE*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for w in warehouse_breakdown[:5]:
                message += f"• {w['warehouse']}: {w['pending_dns']} DNs ({w['pending_units']:,.0f} units) - Rs {w['pending_amount']:,.2f}\n"
            message += "\n"
        
        # City Breakdown
        if city_breakdown:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "🌆 *PENDING BY CITY*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for c in city_breakdown[:5]:
                message += f"• {c['city']}: {c['pending_dns']} DNs ({c['pending_units']:,.0f} units) - Rs {c['pending_amount']:,.2f}\n"
            message += "\n"
        
        # AI Insights Section (Priority 8)
        ai_insights = dashboard.get("ai_insights", {})
        if ai_insights:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "🤖 *AI INSIGHTS*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            
            risk_icon = "🔴" if ai_insights.get("risk_level") == "HIGH" else "🟡" if ai_insights.get("risk_level") == "MEDIUM" else "🟢"
            message += f"{risk_icon} Risk Level: *{ai_insights.get('risk_level', 'UNKNOWN')}*\n\n"
            
            message += "📊 Key Findings:\n"
            for finding in ai_insights.get('findings', [])[:4]:
                message += f"   • {finding}\n"
            
            message += "\n💡 Recommendations:\n"
            for rec in ai_insights.get('recommendations', [])[:4]:
                message += f"   • {rec}\n"
            message += "\n"
        
        # Footer
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += "💡 *Need details on a specific DN?*\n"
        message += "   Reply: *DN <number>*\n"
        message += "━━━━━━━━━━━━━━━━━━━━"
        
        return message
    
    @staticmethod
    def _generate_ai_insights_for_dealer(dashboard: Dict[str, Any]) -> Dict[str, Any]:
        """Generate AI insights for dealer dashboard (Priority 8)"""
        kpis = dashboard.get("kpis", {})
        alerts = dashboard.get("alerts", {})
        pending_dns = dashboard.get("pending_dns", [])
        pod_pending_dns = dashboard.get("pod_pending_dns", [])
        
        findings = []
        recommendations = []
        risk_level = "LOW"
        
        # Analyze pending DNs
        pending_dns_count = kpis.get("pending_dns", 0)
        pod_pending_dns_count = kpis.get("pod_pending_dns", 0)
        total_dns = kpis.get("total_dns", 1)
        
        pending_ratio = (pending_dns_count + pod_pending_dns_count) / total_dns if total_dns > 0 else 0
        
        if pending_ratio > 0.5:
            risk_level = "HIGH"
            findings.append(f"{pending_ratio * 100:.0f}% of deliveries are outstanding")
            recommendations.append("🚨 PRIORITY: Clear pending dispatches immediately")
        elif pending_ratio > 0.25:
            risk_level = "MEDIUM"
            findings.append(f"{pending_ratio * 100:.0f}% of deliveries are pending")
            recommendations.append("Review pending DNs and expedite processing")
        else:
            findings.append("Most deliveries are on track")
        
        # Check oldest pending dispatch
        oldest_pending = alerts.get("oldest_pending_dn")
        if oldest_pending and oldest_pending.get("dispatch_age", 0) > 15:
            risk_level = "HIGH"
            findings.append(f"DN {oldest_pending['dn_no']} has been pending dispatch for {oldest_pending['dispatch_age']} days")
            recommendations.append(f"ESCALATE: DN {oldest_pending['dn_no']} requires urgent dispatch")
        elif oldest_pending and oldest_pending.get("dispatch_age", 0) > 7:
            if risk_level != "HIGH":
                risk_level = "MEDIUM"
            findings.append(f"DN {oldest_pending['dn_no']} pending dispatch for {oldest_pending['dispatch_age']} days")
            recommendations.append(f"Follow up on DN {oldest_pending['dn_no']}")
        
        # Check POD pending aging
        oldest_pod = alerts.get("oldest_pod_pending_dn")
        if oldest_pod and oldest_pod.get("pod_age", 0) > 15:
            risk_level = "HIGH"
            findings.append(f"DN {oldest_pod['dn_no']} delivered but not acknowledged for {oldest_pod['pod_age']} days")
            recommendations.append(f"URGENT: Contact dealer for POD of DN {oldest_pod['dn_no']}")
        elif oldest_pod and oldest_pod.get("pod_age", 0) > 7:
            if risk_level != "HIGH":
                risk_level = "MEDIUM"
            findings.append(f"DN {oldest_pod['dn_no']} awaiting POD for {oldest_pod['pod_age']} days")
            recommendations.append(f"Send reminder for DN {oldest_pod['dn_no']} POD")
        
        # Volume-based insights
        if pending_dns_count > 20:
            findings.append(f"High volume of pending dispatches: {pending_dns_count} DNs")
            recommendations.append("Consider warehouse capacity review")
        
        if pod_pending_dns_count > 15:
            findings.append(f"High volume of POD pending: {pod_pending_dns_count} DNs")
            recommendations.append("Dealer follow-up campaign needed")
        
        # Product insights
        highest_product = alerts.get("highest_pending_product", {})
        if highest_product.get("pending_quantity", 0) > 500:
            findings.append(f"Critical backlog: {highest_product['pending_quantity']:.0f} units of {highest_product['product_name']}")
            recommendations.append(f"Check stock and supply chain for {highest_product['product_name']}")
        elif highest_product.get("pending_quantity", 0) > 100:
            findings.append(f"High pending quantity ({highest_product['pending_quantity']:.0f} units) of {highest_product['product_name']}")
            recommendations.append(f"Review {highest_product['product_name']} orders")
        
        # Value-based insights
        pending_amount = kpis.get("pending_amount", 0)
        pod_pending_amount = kpis.get("pod_pending_amount", 0)
        if pending_amount > 10000000:
            findings.append(f"High financial exposure: Rs {pending_amount:,.2f} pending dispatch")
            recommendations.append("Financial risk review recommended")
        
        # Calculate risk score (0-100, higher = more risk)
        risk_score = min(100, int(
            (pending_ratio * 60) +
            (min(1, (oldest_pending.get("dispatch_age", 0) / 30) if oldest_pending else 0) * 20) +
            (min(1, (oldest_pod.get("pod_age", 0) / 30) if oldest_pod else 0) * 20)
        ))
        
        return {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "findings": findings,
            "recommendations": recommendations,
            "summary": f"Risk Level: {risk_level} (Score: {risk_score}/100) - {len(findings)} issues identified"
        }
    
    # ======================================================
    # EXECUTIVE SUMMARY UPGRADE (Priority 3)
    # ======================================================
    
    @staticmethod
    def get_executive_summary_enhanced(db: Session) -> Dict[str, Any]:
        """
        Enhanced executive summary with Top Risk Dealers, Warehouses, Cities, and Oldest POD DNs.
        Priority 3: Complete executive dashboard for CEO/Management
        """
        
        # Get base metrics
        all_records = db.query(DeliveryReport).all()
        unique_dns = LogisticsQueryService._get_unique_dns(all_records)
        
        # Calculate KPIs
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        pending_dns = LogisticsQueryService._get_unique_dns(pending_records)
        
        delivered_records = [r for r in all_records if r.pgi_status == "Completed"]
        delivered_dns = LogisticsQueryService._get_unique_dns(delivered_records)
        
        pod_pending_records = [r for r in all_records if r.pgi_status == "Completed" and r.pod_status == "Pending"]
        pod_pending_dns = LogisticsQueryService._get_unique_dns(pod_pending_records)
        
        acknowledged_records = [r for r in all_records if r.pod_status == "Received"]
        acknowledged_dns = LogisticsQueryService._get_unique_dns(acknowledged_records)
        
        total_amount = sum(r.dn_amount or 0 for r in all_records)
        pending_amount = sum(r.dn_amount or 0 for r in pending_records)
        pod_pending_amount = sum(r.dn_amount or 0 for r in pod_pending_records)
        
        # ==================================================
        # TOP RISK DEALERS (Priority 3)
        # ==================================================
        dealer_risk = {}
        for r in pending_records:
            if r.customer_name:
                if r.customer_name not in dealer_risk:
                    dealer_risk[r.customer_name] = {
                        "dns": set(),
                        "quantity": 0,
                        "amount": 0,
                        "max_age": 0
                    }
                dealer_risk[r.customer_name]["dns"].add(r.dn_no)
                dealer_risk[r.customer_name]["quantity"] += float(r.dn_qty or 0)
                dealer_risk[r.customer_name]["amount"] += float(r.dn_amount or 0)
                age = LogisticsQueryService.calculate_dispatch_age(r)
                if age > dealer_risk[r.customer_name]["max_age"]:
                    dealer_risk[r.customer_name]["max_age"] = age
        
        top_risk_dealers = sorted(
            [{
                "dealer": d,
                "pending_dns": len(stats["dns"]),
                "units": round(stats["quantity"], 0),
                "value": round(stats["amount"], 2),
                "max_age_days": stats["max_age"]
            } for d, stats in dealer_risk.items()],
            key=lambda x: x["pending_dns"],
            reverse=True
        )[:10]
        
        # ==================================================
        # TOP RISK WAREHOUSES (Priority 3)
        # ==================================================
        warehouse_risk = {}
        for r in pending_records:
            if r.warehouse:
                if r.warehouse not in warehouse_risk:
                    warehouse_risk[r.warehouse] = {
                        "dns": set(),
                        "quantity": 0,
                        "amount": 0,
                        "max_age": 0
                    }
                warehouse_risk[r.warehouse]["dns"].add(r.dn_no)
                warehouse_risk[r.warehouse]["quantity"] += float(r.dn_qty or 0)
                warehouse_risk[r.warehouse]["amount"] += float(r.dn_amount or 0)
                age = LogisticsQueryService.calculate_dispatch_age(r)
                if age > warehouse_risk[r.warehouse]["max_age"]:
                    warehouse_risk[r.warehouse]["max_age"] = age
        
        top_risk_warehouses = sorted(
            [{
                "warehouse": w,
                "pending_dns": len(stats["dns"]),
                "units": round(stats["quantity"], 0),
                "value": round(stats["amount"], 2),
                "max_age_days": stats["max_age"]
            } for w, stats in warehouse_risk.items()],
            key=lambda x: x["pending_dns"],
            reverse=True
        )[:10]
        
        # ==================================================
        # TOP RISK CITIES (Priority 3)
        # ==================================================
        city_risk = {}
        for r in pending_records:
            if r.ship_to_city:
                if r.ship_to_city not in city_risk:
                    city_risk[r.ship_to_city] = {
                        "dns": set(),
                        "quantity": 0,
                        "amount": 0,
                        "max_age": 0
                    }
                city_risk[r.ship_to_city]["dns"].add(r.dn_no)
                city_risk[r.ship_to_city]["quantity"] += float(r.dn_qty or 0)
                city_risk[r.ship_to_city]["amount"] += float(r.dn_amount or 0)
                age = LogisticsQueryService.calculate_dispatch_age(r)
                if age > city_risk[r.ship_to_city]["max_age"]:
                    city_risk[r.ship_to_city]["max_age"] = age
        
        top_risk_cities = sorted(
            [{
                "city": c,
                "pending_dns": len(stats["dns"]),
                "units": round(stats["quantity"], 0),
                "value": round(stats["amount"], 2),
                "max_age_days": stats["max_age"]
            } for c, stats in city_risk.items()],
            key=lambda x: x["pending_dns"],
            reverse=True
        )[:10]
        
        # ==================================================
        # TOP 10 OLDEST POD DNs (Priority 3)
        # ==================================================
        pod_oldest = []
        for r in pod_pending_records:
            pod_age = LogisticsQueryService.calculate_pod_age(r)
            pod_oldest.append({
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "pgi_date": r.good_issue_date,
                "pod_age_days": pod_age,
                "quantity": float(r.dn_qty or 0),
                "value": float(r.dn_amount or 0)
            })
        
        # Deduplicate by DN
        seen_dns = set()
        unique_pod_oldest = []
        for pod in sorted(pod_oldest, key=lambda x: x["pod_age_days"], reverse=True):
            if pod["dn_no"] not in seen_dns:
                seen_dns.add(pod["dn_no"])
                unique_pod_oldest.append(pod)
        
        oldest_pod_dns = unique_pod_oldest[:10]
        
        # ==================================================
        # COMPLETE EXECUTIVE SUMMARY
        # ==================================================
        completion_rate = round((len(delivered_dns) / len(unique_dns) * 100) if unique_dns else 0, 2)
        avg_dispatch_days = 0
        dispatch_ages = [LogisticsQueryService.calculate_dispatch_age(r) for r in delivered_records if r.good_issue_date]
        if dispatch_ages:
            avg_dispatch_days = round(sum(dispatch_ages) / len(dispatch_ages), 1)
        
        summary_data = {
            "success": True,
            "overview": {
                "total_dns": len(unique_dns),
                "total_units": round(sum(r.dn_qty or 0 for r in all_records), 0),
                "total_value": round(total_amount, 2),
                "completion_rate": completion_rate,
                "avg_dispatch_days": avg_dispatch_days
            },
            "pending_status": {
                "pending_dispatch_dns": len(pending_dns),
                "pending_dispatch_units": round(sum(r.dn_qty or 0 for r in pending_records), 0),
                "pending_dispatch_value": round(pending_amount, 2),
                "pending_pod_dns": len(pod_pending_dns),
                "pending_pod_units": round(sum(r.dn_qty or 0 for r in pod_pending_records), 0),
                "pending_pod_value": round(pod_pending_amount, 2),
                "outstanding_value": round(pending_amount + pod_pending_amount, 2)
            },
            "top_risk_dealers": top_risk_dealers,
            "top_risk_warehouses": top_risk_warehouses,
            "top_risk_cities": top_risk_cities,
            "oldest_pod_dns": oldest_pod_dns
        }
        
        # Generate formatted WhatsApp message for executive
        summary_data["formatted_message"] = LogisticsQueryService._format_executive_summary(summary_data)
        
        # Add AI recommendations (Priority 8)
        summary_data["ai_recommendations"] = LogisticsQueryService._generate_executive_ai_recommendations(summary_data)
        
        return summary_data
    
    @staticmethod
    def _format_executive_summary(data: Dict[str, Any]) -> str:
        """Format executive summary for WhatsApp (Priority 3)"""
        overview = data.get("overview", {})
        pending = data.get("pending_status", {})
        top_dealers = data.get("top_risk_dealers", [])[:5]
        top_warehouses = data.get("top_risk_warehouses", [])[:5]
        top_cities = data.get("top_risk_cities", [])[:5]
        oldest_pod = data.get("oldest_pod_dns", [])[:10]
        
        message = "👔 *EXECUTIVE LOGISTICS COMMAND CENTER*\n\n"
        
        # Overview Section
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += "📊 *OVERVIEW PERFORMANCE*\n"
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += f"📦 Total DNs: *{overview.get('total_dns', 0)}*\n"
        message += f"📦 Total Units: *{overview.get('total_units', 0):,.0f}*\n"
        message += f"💰 Total Value: *Rs {overview.get('total_value', 0):,.2f}*\n"
        message += f"✅ Completion Rate: *{overview.get('completion_rate', 0)}%*\n"
        message += f"⏱️ Avg Dispatch Days: *{overview.get('avg_dispatch_days', 0)}* days\n\n"
        
        # Pending Status
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += "⏳ *PENDING STATUS*\n"
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += f"🚚 Pending Dispatch: *{pending.get('pending_dispatch_dns', 0)}* DNs\n"
        message += f"   Units: {pending.get('pending_dispatch_units', 0):,.0f}\n"
        message += f"   Value: Rs {pending.get('pending_dispatch_value', 0):,.2f}\n\n"
        message += f"📋 Pending POD: *{pending.get('pending_pod_dns', 0)}* DNs\n"
        message += f"   Units: {pending.get('pending_pod_units', 0):,.0f}\n"
        message += f"   Value: Rs {pending.get('pending_pod_value', 0):,.2f}\n\n"
        message += f"💰 Total Outstanding: *Rs {pending.get('outstanding_value', 0):,.2f}*\n\n"
        
        # Top Risk Dealers
        if top_dealers:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "🚨 *TOP RISK DEALERS*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for i, d in enumerate(top_dealers, 1):
                message += f"{i}. *{d['dealer']}*\n"
                message += f"   Pending: {d['pending_dns']} DNs | {d['units']:,.0f} units\n"
                message += f"   Value: Rs {d['value']:,.2f} | Max Age: {d['max_age_days']} days\n\n"
        
        # Top Risk Warehouses
        if top_warehouses:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "🏭 *TOP RISK WAREHOUSES*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for i, w in enumerate(top_warehouses, 1):
                message += f"{i}. *{w['warehouse']}*\n"
                message += f"   Pending: {w['pending_dns']} DNs | {w['units']:,.0f} units\n"
                message += f"   Value: Rs {w['value']:,.2f} | Max Age: {w['max_age_days']} days\n\n"
        
        # Top Risk Cities
        if top_cities:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "🌆 *TOP RISK CITIES*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for i, c in enumerate(top_cities, 1):
                message += f"{i}. *{c['city']}*\n"
                message += f"   Pending: {c['pending_dns']} DNs | {c['units']:,.0f} units\n"
                message += f"   Value: Rs {c['value']:,.2f} | Max Age: {c['max_age_days']} days\n\n"
        
        # Oldest POD DNs
        if oldest_pod:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "📋 *TOP 10 OLDEST POD PENDING*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for i, pod in enumerate(oldest_pod, 1):
                pgi_date = ""
                if pod.get('pgi_date'):
                    if isinstance(pod['pgi_date'], datetime):
                        pgi_date = pod['pgi_date'].strftime('%d-%b')
                    else:
                        pgi_date = str(pod['pgi_date'])[:5]
                message += f"{i}. *DN {pod['dn_no']}*\n"
                message += f"   Dealer: {pod['dealer']}\n"
                message += f"   PGI Date: {pgi_date} | POD Age: {pod['pod_age_days']} days\n"
                message += f"   Qty: {pod['quantity']:,.0f} | Value: Rs {pod['value']:,.2f}\n\n"
        
        # AI Recommendations
        ai_recs = data.get("ai_recommendations", {})
        if ai_recs:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "🤖 *AI ACTION PLAN FOR TODAY*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for rec in ai_recs.get("recommendations", [])[:5]:
                priority_icon = "🔴" if rec.get("priority") == "HIGH" else "🟡" if rec.get("priority") == "MEDIUM" else "🟢"
                message += f"{priority_icon} *{rec.get('title', 'Action')}*\n"
                message += f"   {rec.get('description', '')}\n\n"
        
        # Footer
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += "💡 *Need more details?*\n"
        message += "   Reply: *DETAILS <dealer/warehouse/city>*\n"
        message += "━━━━━━━━━━━━━━━━━━━━"
        
        return message
    
    @staticmethod
    def _generate_executive_ai_recommendations(data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate AI recommendations for executive (Priority 8)"""
        recommendations = []
        pending = data.get("pending_status", {})
        top_dealers = data.get("top_risk_dealers", [])
        top_warehouses = data.get("top_risk_warehouses", [])
        top_cities = data.get("top_risk_cities", [])
        oldest_pod = data.get("oldest_pod_dns", [])
        
        # Priority 1: Clear oldest POD pending
        if oldest_pod:
            oldest = oldest_pod[0]
            recommendations.append({
                "title": "Clear Oldest POD Pending",
                "description": f"DN {oldest['dn_no']} to {oldest['dealer']} has been awaiting acknowledgement for {oldest['pod_age_days']} days",
                "priority": "HIGH",
                "action": "Contact dealer immediately for POD submission"
            })
        
        # Priority 2: Address top risk dealer
        if top_dealers:
            worst_dealer = top_dealers[0]
            recommendations.append({
                "title": "Escalate Top Risk Dealer",
                "description": f"{worst_dealer['dealer']} has {worst_dealer['pending_dns']} pending DNs worth Rs {worst_dealer['value']:,.2f}",
                "priority": "HIGH",
                "action": "Schedule urgent review meeting with dealer management"
            })
        
        # Priority 3: Warehouse bottleneck
        if top_warehouses:
            worst_warehouse = top_warehouses[0]
            recommendations.append({
                "title": "Warehouse Bottleneck Alert",
                "description": f"{worst_warehouse['warehouse']} has {worst_warehouse['pending_dns']} pending DNs with max age {worst_warehouse['max_age_days']} days",
                "priority": "HIGH",
                "action": "Review warehouse capacity and staffing"
            })
        
        # Priority 4: City focus
        if top_cities:
            worst_city = top_cities[0]
            recommendations.append({
                "title": "Regional Focus Required",
                "description": f"{worst_city['city']} region has {worst_city['pending_dns']} pending deliveries",
                "priority": "MEDIUM",
                "action": "Deploy additional logistics resources to this region"
            })
        
        # Priority 5: Financial exposure
        if pending.get("outstanding_value", 0) > 50000000:
            recommendations.append({
                "title": "Financial Risk Alert",
                "description": f"Total outstanding value is Rs {pending.get('outstanding_value', 0):,.2f}",
                "priority": "HIGH",
                "action": "Review credit terms and collection process"
            })
        
        return {
            "recommendations": recommendations,
            "summary": f"Found {len(recommendations)} critical action items for today"
        }
    
    # ======================================================
    # INTENT DETECTION (UPDATED)
    # ======================================================
    
    @staticmethod
    def detect_intent(question: str) -> Dict[str, Any]:
        """Detect user intent from natural language question."""
        original_question = question
        question_lower = LogisticsQueryService._normalize_question(question)
        
        # DN NUMBER DETECTION
        dn_match = re.search(r'\b(\d{8,15})\b', question)
        if dn_match:
            return {"intent": "dn_lookup", "dn_no": dn_match.group(1)}
        
        dn_keywords = ["dn", "delivery note", "delivery number", "status of dn", "check dn", "show dn"]
        for keyword in dn_keywords:
            if keyword in question_lower:
                dn_match_with_keyword = re.search(r'\b(\d{8,15})\b', question)
                if dn_match_with_keyword:
                    return {"intent": "dn_lookup", "dn_no": dn_match_with_keyword.group(1)}
        
        # DEALER DASHBOARD - Universal dealer intelligence
        dealer_indicators = [
            "dealer", "customer", "show dealer", "dealer summary", "dealer dashboard",
            "status of", "show me", "tell me about", "deliveries of", "delivery for",
            "information about", "details of", "dealer aging", "dealer performance"
        ]
        
        for indicator in dealer_indicators:
            if indicator in question_lower:
                parts = question_lower.split(indicator)
                if len(parts) > 1:
                    dealer_name = parts[1].strip().title()
                    if dealer_name and len(dealer_name) > 2:
                        page_match = re.search(r'page\s+(\d+)', question_lower)
                        page = int(page_match.group(1)) if page_match else 1
                        return {"intent": "dealer_dashboard", "dealer_name": dealer_name, "page": page}
        
        # Short name query (single word dealer name)
        if 2 < len(original_question) < 30 and not re.search(r'\d', original_question):
            return {"intent": "dealer_dashboard", "dealer_name": original_question.strip().title(), "page": 1}
        
        # Executive/CEO queries
        executive_keywords = ["ceo", "executive", "command center", "executive summary", "ceo dashboard", "what should i focus on"]
        if any(keyword in question_lower for keyword in executive_keywords):
            return {"intent": "executive_summary_enhanced"}
        
        # Top risk queries
        if any(phrase in question_lower for phrase in ["top risk", "worst dealer", "worst warehouse", "worst city"]):
            return {"intent": "executive_summary_enhanced"}
        
        # Aging queries (using corrected calculations)
        if any(phrase in question_lower for phrase in ["aging report", "delivery aging", "pending aging"]):
            return {"intent": "pending_delivery_aging"}
        
        if any(phrase in question_lower for phrase in ["warehouse aging", "oldest pending warehouse"]):
            return {"intent": "warehouse_aging"}
        
        if any(phrase in question_lower for phrase in ["city aging", "oldest pending city"]):
            return {"intent": "city_aging"}
        
        # Critical deliveries
        if any(phrase in question_lower for phrase in ["critical deliveries", "critical pending", "urgent deliveries"]):
            return {"intent": "critical_pending_dns"}
        
        # Pending counts
        pending_keywords = ["pending delivery", "pending deliveries", "how many pending", "pending orders"]
        if any(keyword in question_lower for keyword in pending_keywords):
            return {"intent": "pending_deliveries"}
        
        # Default
        return {"intent": "general_query", "question": original_question}
    
    # ======================================================
    # EXISTING FUNCTIONS (KEPT FOR BACKWARD COMPATIBILITY)
    # ======================================================
    
    @staticmethod
    def search_dealer_fuzzy(db: Session, dealer_name: str, limit: int = 5) -> Dict[str, Any]:
        """Search dealers by partial name match."""
        dealers = (
            db.query(
                DeliveryReport.customer_name,
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_amount"),
                func.sum(DeliveryReport.dn_qty).label("total_quantity")
            )
            .filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
            .group_by(DeliveryReport.customer_name)
            .limit(limit)
            .all()
        )
        
        if not dealers:
            return {"success": False, "message": f"No dealer found matching '{dealer_name}'"}
        
        return {
            "success": True,
            "search_term": dealer_name,
            "matches": [
                {
                    "dealer_name": d.customer_name,
                    "total_dns": d.total_dns,
                    "total_amount": float(d.total_amount or 0),
                    "total_quantity": float(d.total_quantity or 0)
                }
                for d in dealers
            ]
        }
    
    @staticmethod
    def get_dn_product_breakdown(db: Session, dn_no: str) -> Dict[str, Any]:
        """Get product breakdown for a single DN with corrected aging."""
        records = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.dn_no == dn_no)
            .all()
        )
        
        if not records:
            return {"success": False, "message": f"DN {dn_no} not found"}
        
        products = [
            {
                "material_no": r.material_no,
                "product_name": r.customer_model or r.material_no,
                "quantity": float(r.dn_qty or 0),
                "amount": float(r.dn_amount or 0)
            }
            for r in records
        ]
        
        main = records[0]
        total_qty = sum(p["quantity"] for p in products)
        total_amount = sum(p["amount"] for p in products)
        
        # Use corrected aging calculations
        aging = LogisticsQueryService.get_dn_aging_details(main)
        
        if main.pgi_status == "Completed" and main.pod_status == "Received":
            status_text = "Delivered and Acknowledged"
        elif main.pgi_status == "Completed" and main.pod_status == "Pending":
            status_text = "Delivered, Awaiting Acknowledgement"
        elif main.pgi_status == "Pending":
            status_text = "Pending Dispatch"
        else:
            status_text = main.delivery_status or "Unknown"
        
        return {
            "success": True,
            "dn_no": dn_no,
            "dealer": main.customer_name,
            "city": main.ship_to_city,
            "warehouse": main.warehouse,
            "status": status_text,
            "pod_status": "Received" if main.pod_status == "Received" else "Pending",
            "dispatch_age": aging["dispatch_age"],
            "pod_age": aging["pod_age"],
            "delivery_cycle": aging["delivery_cycle"],
            "dn_create_date": main.dn_create_date,
            "good_issue_date": main.good_issue_date,
            "pod_date": main.pod_date,
            "products": products,
            "total_quantity": float(total_qty),
            "total_amount": float(total_amount)
        }
    
    # ======================================================
    # AI CONTEXT BUILDER (UPDATED)
    # ======================================================
    
    @staticmethod
    def generate_ai_context(question: str, db: Session) -> Dict[str, Any]:
        """Generate rich AI context from natural language question."""
        intent_result = LogisticsQueryService.detect_intent(question)
        intent = intent_result["intent"]
        
        # Master dealer dashboard routing (Priority 5)
        if intent == "dealer_dashboard":
            dealer_name = intent_result.get("dealer_name", "")
            page = intent_result.get("page", 1)
            result = LogisticsQueryService.get_dealer_complete_dashboard(db, dealer_name, page)
            result["summary"] = result.get("formatted_message", "Dealer dashboard generated.")
        
        # Enhanced Executive Summary (Priority 3)
        elif intent == "executive_summary_enhanced":
            result = LogisticsQueryService.get_executive_summary_enhanced(db)
            result["summary"] = result.get("formatted_message", "Executive summary generated.")
        
        elif intent == "dn_lookup":
            result = LogisticsQueryService.get_dn_product_breakdown(db, intent_result["dn_no"])
            result["summary"] = LogisticsQueryService._format_dn_summary_enhanced(result)
        
        elif intent == "pending_delivery_aging":
            result = LogisticsQueryService._get_pending_delivery_aging_corrected(db)
        
        elif intent == "warehouse_aging":
            result = LogisticsQueryService._get_warehouse_aging_corrected(db)
        
        elif intent == "city_aging":
            result = LogisticsQueryService._get_city_aging_corrected(db)
        
        elif intent == "critical_pending_dns":
            result = LogisticsQueryService._get_critical_pending_dns_corrected(db)
        
        elif intent == "pending_deliveries":
            result = LogisticsQueryService._get_pending_deliveries_corrected(db)
            result["summary"] = f"There are {result.get('count', 0)} pending deliveries totaling {result.get('total_quantity', 0):.0f} units worth Rs {result.get('total_amount', 0):,.2f}."
        
        else:
            # Default to enhanced executive summary
            result = LogisticsQueryService.get_executive_summary_enhanced(db)
            result["summary"] = result.get("formatted_message", "Executive summary generated.")
        
        result["question"] = question
        result["intent"] = intent
        
        return result
    
    @staticmethod
    def _format_dn_summary_enhanced(dn_result: Dict[str, Any]) -> str:
        """Format DN summary with enhanced details (Priority 2 format)."""
        if not dn_result.get("success"):
            return f"DN {dn_result.get('dn_no', 'unknown')} was not found in the system."
        
        dn_no = dn_result.get("dn_no", "Unknown")
        dealer = dn_result.get("dealer", "Unknown Customer")
        city = dn_result.get("city", "Unknown City")
        warehouse = dn_result.get("warehouse", "Unknown Warehouse")
        status = dn_result.get("status", "Unknown")
        pod_status = dn_result.get("pod_status", "Pending")
        dispatch_age = dn_result.get("dispatch_age", 0)
        pod_age = dn_result.get("pod_age", 0)
        total_amount = dn_result.get("total_amount", 0)
        total_quantity = dn_result.get("total_quantity", 0)
        products = dn_result.get("products", [])
        
        # Format dates
        dn_date = ""
        if dn_result.get('dn_create_date'):
            if isinstance(dn_result['dn_create_date'], datetime):
                dn_date = dn_result['dn_create_date'].strftime('%d-%b-%Y')
            else:
                dn_date = str(dn_result['dn_create_date'])[:10]
        
        pgi_date = ""
        if dn_result.get('good_issue_date'):
            if isinstance(dn_result['good_issue_date'], datetime):
                pgi_date = dn_result['good_issue_date'].strftime('%d-%b-%Y')
            else:
                pgi_date = str(dn_result['good_issue_date'])[:10]
        
        summary = f"🔹 *DN: {dn_no}*\n\n"
        summary += f"📋 Dealer: {dealer}\n"
        summary += f"📍 City: {city} | 🏭 Warehouse: {warehouse}\n"
        summary += f"📅 DN Date: {dn_date}\n"
        summary += f"🚚 PGI Date: {pgi_date if pgi_date else 'Not Dispatched'}\n\n"
        summary += f"📋 Status: {status}\n"
        summary += f"📋 POD: {pod_status}\n"
        summary += f"⏱️ Dispatch Age: {dispatch_age} days\n"
        summary += f"⏱️ POD Age: {pod_age} days\n\n"
        summary += f"📦 Total Quantity: {total_quantity:.0f} units\n"
        summary += f"💰 Amount: Rs {total_amount:,.2f}\n\n"
        
        if products:
            summary += "📦 *Products:*\n"
            for p in products[:5]:
                summary += f"   • {p['product_name']}: {p['quantity']:.0f} units\n"
            if len(products) > 5:
                summary += f"   • +{len(products) - 5} more products\n"
        
        if dispatch_age > 15 or pod_age > 15:
            summary += "\n⚠️ *CRITICAL:* This delivery requires immediate attention!"
        
        return summary
    
    # ======================================================
    # CORRECTED INTERNAL METHODS (Using new aging calculations)
    # ======================================================
    
    @staticmethod
    def _get_pending_delivery_aging_corrected(db: Session) -> Dict[str, Any]:
        """Pending delivery aging using corrected dispatch_age."""
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "message": "No pending deliveries found"}
        
        aging_groups = {
            "0-3 Days": {"dns": set(), "quantity": 0, "amount": 0},
            "4-7 Days": {"dns": set(), "quantity": 0, "amount": 0},
            "8-15 Days": {"dns": set(), "quantity": 0, "amount": 0},
            "16-30 Days": {"dns": set(), "quantity": 0, "amount": 0},
            "30+ Days": {"dns": set(), "quantity": 0, "amount": 0}
        }
        
        for r in pending_records:
            age_days = LogisticsQueryService.calculate_dispatch_age(r)
            
            if age_days <= 3:
                aging_groups["0-3 Days"]["dns"].add(r.dn_no)
                aging_groups["0-3 Days"]["quantity"] += float(r.dn_qty or 0)
                aging_groups["0-3 Days"]["amount"] += float(r.dn_amount or 0)
            elif age_days <= 7:
                aging_groups["4-7 Days"]["dns"].add(r.dn_no)
                aging_groups["4-7 Days"]["quantity"] += float(r.dn_qty or 0)
                aging_groups["4-7 Days"]["amount"] += float(r.dn_amount or 0)
            elif age_days <= 15:
                aging_groups["8-15 Days"]["dns"].add(r.dn_no)
                aging_groups["8-15 Days"]["quantity"] += float(r.dn_qty or 0)
                aging_groups["8-15 Days"]["amount"] += float(r.dn_amount or 0)
            elif age_days <= 30:
                aging_groups["16-30 Days"]["dns"].add(r.dn_no)
                aging_groups["16-30 Days"]["quantity"] += float(r.dn_qty or 0)
                aging_groups["16-30 Days"]["amount"] += float(r.dn_amount or 0)
            else:
                aging_groups["30+ Days"]["dns"].add(r.dn_no)
                aging_groups["30+ Days"]["quantity"] += float(r.dn_qty or 0)
                aging_groups["30+ Days"]["amount"] += float(r.dn_amount or 0)
        
        total_pending = sum(len(g["dns"]) for g in aging_groups.values())
        
        summary = f"📊 PENDING DELIVERY AGING REPORT (Dispatch Age)\n\n"
        summary += f"Total Pending DNs: {total_pending}\n\n"
        for category, data in aging_groups.items():
            if len(data["dns"]) > 0:
                summary += f"• {category}: {len(data['dns'])} DNs, {data['quantity']:.0f} units, Rs {data['amount']:,.2f}\n"
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _get_warehouse_aging_corrected(db: Session, limit: int = 10) -> Dict[str, Any]:
        """Warehouse aging using corrected dispatch_age."""
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "warehouses": [], "count": 0}
        
        warehouse_stats = {}
        for r in pending_records:
            if not r.warehouse:
                continue
            if r.warehouse not in warehouse_stats:
                warehouse_stats[r.warehouse] = {"dns": set(), "max_age": 0, "quantity": 0, "amount": 0}
            
            age = LogisticsQueryService.calculate_dispatch_age(r)
            warehouse_stats[r.warehouse]["dns"].add(r.dn_no)
            warehouse_stats[r.warehouse]["quantity"] += float(r.dn_qty or 0)
            warehouse_stats[r.warehouse]["amount"] += float(r.dn_amount or 0)
            
            if age > warehouse_stats[r.warehouse]["max_age"]:
                warehouse_stats[r.warehouse]["max_age"] = age
        
        sorted_warehouses = sorted(warehouse_stats.items(), key=lambda x: x[1]["max_age"], reverse=True)[:limit]
        
        summary = "🏭 WAREHOUSE AGING RANKING (Dispatch Age)\n\n"
        for i, w in enumerate(sorted_warehouses, 1):
            summary += f"{i}. {w[0]}\n"
            summary += f"   Pending DNs: {len(w[1]['dns'])}, Units: {w[1]['quantity']:.0f}, Value: Rs {w[1]['amount']:,.2f}\n"
            summary += f"   Oldest Age: {w[1]['max_age']} days\n\n"
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _get_city_aging_corrected(db: Session, limit: int = 10) -> Dict[str, Any]:
        """City aging using corrected dispatch_age."""
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "cities": [], "count": 0}
        
        city_stats = {}
        for r in pending_records:
            if not r.ship_to_city:
                continue
            if r.ship_to_city not in city_stats:
                city_stats[r.ship_to_city] = {"dns": set(), "max_age": 0, "quantity": 0, "amount": 0}
            
            age = LogisticsQueryService.calculate_dispatch_age(r)
            city_stats[r.ship_to_city]["dns"].add(r.dn_no)
            city_stats[r.ship_to_city]["quantity"] += float(r.dn_qty or 0)
            city_stats[r.ship_to_city]["amount"] += float(r.dn_amount or 0)
            
            if age > city_stats[r.ship_to_city]["max_age"]:
                city_stats[r.ship_to_city]["max_age"] = age
        
        sorted_cities = sorted(city_stats.items(), key=lambda x: x[1]["max_age"], reverse=True)[:limit]
        
        summary = "🌆 CITY AGING RANKING (Dispatch Age)\n\n"
        for i, c in enumerate(sorted_cities, 1):
            summary += f"{i}. {c[0]}\n"
            summary += f"   Pending DNs: {len(c[1]['dns'])}, Units: {c[1]['quantity']:.0f}, Value: Rs {c[1]['amount']:,.2f}\n"
            summary += f"   Oldest Age: {c[1]['max_age']} days\n\n"
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _get_critical_pending_dns_corrected(db: Session, threshold_days: int = 15, limit: int = 20) -> Dict[str, Any]:
        """Critical pending DNs using corrected dispatch_age."""
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        critical_dns = []
        seen_dns = set()
        
        for r in pending_records:
            age_days = LogisticsQueryService.calculate_dispatch_age(r)
            if age_days >= threshold_days and r.dn_no not in seen_dns:
                seen_dns.add(r.dn_no)
                critical_dns.append({
                    "dn_no": r.dn_no,
                    "dealer": r.customer_name,
                    "age_days": age_days,
                    "quantity": float(r.dn_qty or 0),
                    "amount": float(r.dn_amount or 0)
                })
        
        critical_dns.sort(key=lambda x: x["age_days"], reverse=True)
        
        summary = f"🚨 CRITICAL DELIVERIES ({threshold_days}+ days dispatch age)\n\n"
        total_amount = 0
        for dn in critical_dns[:limit]:
            summary += f"• DN {dn['dn_no']}: {dn['dealer']}\n"
            summary += f"   Age: {dn['age_days']} days, Qty: {dn['quantity']:.0f} units, Value: Rs {dn['amount']:,.2f}\n"
            total_amount += dn['amount']
        
        summary += f"\n💰 Total value at risk: Rs {total_amount:,.2f}"
        
        if len(critical_dns) > limit:
            summary += f"\nAnd {len(critical_dns) - limit} more critical deliveries"
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _get_pending_deliveries_corrected(db: Session, limit: int = 100):
        """Get pending deliveries with corrected amounts."""
        rows = db.query(DeliveryReport).filter(DeliveryReport.pgi_status != "Completed").all()
        
        dn_groups = {}
        for r in rows:
            if r.dn_no not in dn_groups:
                dn_groups[r.dn_no] = {"amount": 0, "quantity": 0}
            dn_groups[r.dn_no]["amount"] += float(r.dn_amount or 0)
            dn_groups[r.dn_no]["quantity"] += float(r.dn_qty or 0)
        
        total_amount = sum(g["amount"] for g in dn_groups.values())
        total_quantity = sum(g["quantity"] for g in dn_groups.values())
        
        return {
            "success": True,
            "count": len(dn_groups),
            "total_amount": float(total_amount),
            "total_quantity": float(total_quantity)
        }
    
    # ======================================================
    # GPT PROMPT BUILDER & UNIFIED HANDLER
    # ======================================================
    
    @staticmethod
    def build_gpt_prompt(question: str, context: Dict[str, Any]) -> str:
        """Build a professional prompt for GPT/OpenAI."""
        intent = context.get("intent", "general_query")
        summary = context.get("summary", "No summary available")
        
        prompt = f"""You are a professional Logistics Operations Manager.

USER QUESTION: {question}

INTENT: {intent}

DATA: {summary}

BUSINESS RULES:
- PGI Completed = Delivered
- PGI Pending = Pending Dispatch
- POD Received = Acknowledged
- POD Pending = Awaiting Acknowledgement
- Dispatch Age > 15 days = Critical
- POD Age > 15 days = Urgent

Never show PGI/POD. Use: Delivered, Pending Dispatch, Acknowledged, Awaiting Acknowledgement

RESPONSE:"""
        
        return prompt
    
    @staticmethod
    def handle_ai_query(question: str, db: Session, openai_client=None) -> Dict[str, Any]:
        """Complete AI-powered query handler for WhatsApp."""
        context = LogisticsQueryService.generate_ai_context(question, db)
        prompt = LogisticsQueryService.build_gpt_prompt(question, context)
        
        ai_response = context.get("summary", "Query processed successfully.")
        
        return {
            "success": True,
            "question": question,
            "intent": context.get("intent"),
            "summary": context.get("summary"),
            "ai_response": ai_response,
            "data": context.get("records", []),
            "metadata": {
                "total_records": len(context.get("records", [])),
                "has_data": bool(context.get("records"))
            }
        }


# ======================================================
# CONVENIENCE FUNCTIONS
# ======================================================

def handle_logistics_query(question: str, db: Session) -> Dict[str, Any]:
    return LogisticsQueryService.generate_ai_context(question, db)

def handle_ai_query(question: str, db: Session, openai_client=None) -> Dict[str, Any]:
    return LogisticsQueryService.handle_ai_query(question, db, openai_client)

def get_ai_context(question: str, db: Session) -> Dict[str, Any]:
    return LogisticsQueryService.generate_ai_context(question, db)

def build_gpt_prompt(question: str, context: Dict[str, Any]) -> str:
    return LogisticsQueryService.build_gpt_prompt(question, context)
