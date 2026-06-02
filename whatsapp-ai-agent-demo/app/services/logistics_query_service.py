# ==========================================================
# FILE: app/services/logistics_query_service.py
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
    def _calculate_dn_age(record) -> int:
        """Calculate age of a DN in days from creation date."""
        if not record.dn_create_date:
            return 0
        
        if isinstance(record.dn_create_date, datetime):
            create_date = record.dn_create_date.date()
        else:
            create_date = record.dn_create_date
        
        return (datetime.now().date() - create_date).days
    
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
            "dn_age_days": LogisticsQueryService._calculate_dn_age(record)
        }
    
    # ======================================================
    # MASTER DEALER DASHBOARD BUILDER (NEW - REPLACES MULTIPLE FUNCTIONS)
    # ======================================================
    
    @staticmethod
    def get_dealer_complete_dashboard(db: Session, dealer_name: str, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        """
        Master function that builds a complete dealer dashboard in one pass.
        This replaces get_dealer_summary, get_dealer_dn_breakdown, get_dealer_product_summary, and get_dealer_aging.
        
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
        
        # Step 2: Group by unique DN
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
                
                dn_groups[dn_no] = {
                    "dn_no": dn_no,
                    "status": status,
                    "status_text": status_text,
                    "pod_status": "Received" if r.pod_status == "Received" else "Pending",
                    "total_quantity": 0,
                    "total_amount": 0,
                    "age_days": LogisticsQueryService._calculate_dn_age(r),
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
                    warehouse_stats[r.warehouse] = {"dns": set(), "quantity": 0}
                warehouse_stats[r.warehouse]["dns"].add(dn_no)
                warehouse_stats[r.warehouse]["quantity"] += float(r.dn_qty or 0)
            
            # City stats (for pending only)
            if r.pgi_status != "Completed" and r.ship_to_city:
                if r.ship_to_city not in city_stats:
                    city_stats[r.ship_to_city] = {"dns": set(), "quantity": 0}
                city_stats[r.ship_to_city]["dns"].add(dn_no)
                city_stats[r.ship_to_city]["quantity"] += float(r.dn_qty or 0)
        
        # Step 3: Categorize DNs by status
        delivered_dns = [dn for dn in dn_groups.values() if dn["status"] == "delivered_acknowledged"]
        pending_dns = [dn for dn in dn_groups.values() if dn["status"] == "pending"]
        pod_pending_dns = [dn for dn in dn_groups.values() if dn["status"] == "delivered_not_acknowledged"]
        
        # Sort pending DNs by age (oldest first)
        pending_dns_sorted = sorted(pending_dns, key=lambda x: x["age_days"], reverse=True)
        pod_pending_dns_sorted = sorted(pod_pending_dns, key=lambda x: x["age_days"], reverse=True)
        
        # Step 4: Calculate KPIs
        total_dns = len(dn_groups)
        total_units = sum(dn["total_quantity"] for dn in dn_groups.values())
        delivered_units = sum(dn["total_quantity"] for dn in delivered_dns)
        pending_units = sum(dn["total_quantity"] for dn in pending_dns)
        pod_pending_units = sum(dn["total_quantity"] for dn in pod_pending_dns)
        
        total_amount = sum(dn["total_amount"] for dn in dn_groups.values())
        pending_amount = sum(dn["total_amount"] for dn in pending_dns)
        
        # Step 5: Find oldest pending DN
        oldest_pending = pending_dns_sorted[0] if pending_dns_sorted else None
        oldest_pod_pending = pod_pending_dns_sorted[0] if pod_pending_dns_sorted else None
        
        # Step 6: Find highest pending product
        highest_pending_product = max(
            [(p["product_name"], p["pending_quantity"]) for p in product_summary.values()],
            key=lambda x: x[1],
            default=("None", 0)
        )
        
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
                "pod_pending_units": round(pod_pending_units, 0)
            },
            "alerts": {
                "oldest_pending_dn": {
                    "dn_no": oldest_pending["dn_no"] if oldest_pending else None,
                    "age_days": oldest_pending["age_days"] if oldest_pending else 0,
                    "quantity": oldest_pending["total_quantity"] if oldest_pending else 0
                } if oldest_pending else None,
                "oldest_pod_pending_dn": {
                    "dn_no": oldest_pod_pending["dn_no"] if oldest_pod_pending else None,
                    "age_days": oldest_pod_pending["age_days"] if oldest_pod_pending else 0,
                    "quantity": oldest_pod_pending["total_quantity"] if oldest_pod_pending else 0
                } if oldest_pod_pending else None,
                "highest_pending_product": {
                    "product_name": highest_pending_product[0],
                    "pending_quantity": round(highest_pending_product[1], 0)
                }
            },
            "pending_dns": paginated_pending_dns,
            "pod_pending_dns": pod_pending_dns_sorted[:10],  # Top 10 POD pending for quick view
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
                [{"warehouse": w, "pending_dns": len(stats["dns"]), "pending_units": round(stats["quantity"], 0)}
                 for w, stats in warehouse_stats.items()],
                key=lambda x: x["pending_dns"],
                reverse=True
            )[:10],
            "city_breakdown": sorted(
                [{"city": c, "pending_dns": len(stats["dns"]), "pending_units": round(stats["quantity"], 0)}
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
        
        # Step 9: Generate formatted WhatsApp message
        dashboard["formatted_message"] = LogisticsQueryService._format_dealer_dashboard(dashboard)
        
        return dashboard
    
    @staticmethod
    def _format_dealer_dashboard(dashboard: Dict[str, Any]) -> str:
        """Format the dealer dashboard into a WhatsApp-friendly message."""
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
        message += f"📋 *Pending POD:* {kpis['pod_pending_dns']} DNs ({kpis['pod_pending_units']:,.0f} units)\n"
        message += f"💰 *Pending Value:* Rs {kpis['pending_amount']:,.2f}\n\n"
        
        # Alerts Section (Critical)
        if alerts["oldest_pending_dn"] and alerts["oldest_pending_dn"]["dn_no"]:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "🚨 *CRITICAL ALERTS*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += f"⚠️ *Oldest Pending DN:* {alerts['oldest_pending_dn']['dn_no']}\n"
            message += f"   Age: {alerts['oldest_pending_dn']['age_days']} days\n"
            message += f"   Quantity: {alerts['oldest_pending_dn']['quantity']:,.0f} units\n\n"
        
        if alerts["oldest_pod_pending_dn"] and alerts["oldest_pod_pending_dn"]["dn_no"]:
            message += f"⚠️ *Oldest POD Pending:* {alerts['oldest_pod_pending_dn']['dn_no']}\n"
            message += f"   Age: {alerts['oldest_pod_pending_dn']['age_days']} days\n\n"
        
        if alerts["highest_pending_product"]["product_name"] != "None":
            message += f"⚠️ *Highest Pending Product:* {alerts['highest_pending_product']['product_name']}\n"
            message += f"   Pending Quantity: {alerts['highest_pending_product']['pending_quantity']:,.0f} units\n\n"
        
        # Pending DNs Section (Paginated)
        if pending_dns:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += f"⏳ *PENDING DNs (Page {pagination.get('page', 1)} of {pagination.get('total_pages', 1)})*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for dn in pending_dns[:10]:
                message += f"🔹 *DN {dn['dn_no']}*\n"
                message += f"   Age: {dn['age_days']} days | Qty: {dn['total_quantity']:,.0f} units\n"
                # Show top 3 products
                products_display = dn['products'][:3]
                for p in products_display:
                    message += f"   • {p['product_name']}: {p['quantity']:,.0f} units\n"
                if len(dn['products']) > 3:
                    message += f"   • +{len(dn['products']) - 3} more products\n"
                message += "\n"
            
            if pagination.get("has_next", False):
                message += f"📌 *{pagination.get('total_pending_dns', 0) - 10} more pending DNs*\n"
                message += f"   Reply: *MORE PENDING* for next page\n\n"
        
        # POD Pending DNs (Top 5)
        if pod_pending_dns:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "📋 *AWAITING ACKNOWLEDGEMENT*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for dn in pod_pending_dns[:5]:
                message += f"🔹 *DN {dn['dn_no']}* - Age: {dn['age_days']} days | Qty: {dn['total_quantity']:,.0f} units\n"
            if len(pod_pending_dns) > 5:
                message += f"\n📌 +{len(pod_pending_dns) - 5} more POD pending DNs\n"
            message += "\n"
        
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
                message += f"• {w['warehouse']}: {w['pending_dns']} DNs ({w['pending_units']:,.0f} units)\n"
            message += "\n"
        
        # City Breakdown
        if city_breakdown:
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            message += "🌆 *PENDING BY CITY*\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
            for c in city_breakdown[:5]:
                message += f"• {c['city']}: {c['pending_dns']} DNs ({c['pending_units']:,.0f} units)\n"
            message += "\n"
        
        # Footer
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += "💡 *Need details on a specific DN?*\n"
        message += "   Reply: *DN <number>*\n"
        message += "━━━━━━━━━━━━━━━━━━━━"
        
        return message
    
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
        
        # DEALER DASHBOARD (NEW - Routes all dealer queries to master dashboard)
        dealer_indicators = [
            "dealer", "customer", "show dealer", "dealer summary", "dealer dashboard",
            "status of", "show me", "tell me about", "deliveries of", "delivery for",
            "information about", "details of", "dealer aging", "dealer performance"
        ]
        
        # Check for dealer name patterns
        for indicator in dealer_indicators:
            if indicator in question_lower:
                # Extract dealer name after the indicator
                parts = question_lower.split(indicator)
                if len(parts) > 1:
                    dealer_name = parts[1].strip().title()
                    if dealer_name and len(dealer_name) > 2:
                        # Check for page number in question
                        page_match = re.search(r'page\s+(\d+)', question_lower)
                        page = int(page_match.group(1)) if page_match else 1
                        return {"intent": "dealer_dashboard", "dealer_name": dealer_name, "page": page}
        
        # Check for "more pending" pagination
        if any(phrase in question_lower for phrase in ["more pending", "next page", "show more"]):
            return {"intent": "dealer_pagination", "direction": "next"}
        
        # If question is short (2-30 chars) and not a number, treat as dealer dashboard
        if 2 < len(original_question) < 30 and not re.search(r'\d', original_question):
            return {"intent": "dealer_dashboard", "dealer_name": original_question.strip().title(), "page": 1}
        
        # ==================================================
        # AGING & DELIVERY INTELLIGENCE
        # ==================================================
        
        if any(phrase in question_lower for phrase in [
            "aging report", "delivery aging", "pending aging", "old pending", "oldest pending"
        ]):
            return {"intent": "pending_delivery_aging"}
        
        if any(phrase in question_lower for phrase in [
            "warehouse aging", "which warehouse has the oldest", "oldest pending warehouse"
        ]):
            return {"intent": "warehouse_aging"}
        
        if any(phrase in question_lower for phrase in [
            "city aging", "which city has the oldest", "oldest pending city"
        ]):
            return {"intent": "city_aging"}
        
        if any(phrase in question_lower for phrase in [
            "critical deliveries", "critical pending", "urgent deliveries"
        ]):
            return {"intent": "critical_pending_dns"}
        
        if any(phrase in question_lower for phrase in [
            "delivery risk", "biggest risks", "risk summary", "what are the biggest delivery risks"
        ]):
            return {"intent": "risk_summary"}
        
        if any(phrase in question_lower for phrase in [
            "dealer with most delays", "most delayed dealer", "top delayed dealer"
        ]):
            return {"intent": "top_delayed_dealers"}
        
        if any(phrase in question_lower for phrase in [
            "sla breach", "breaching sla", "sla violation", "deliveries older than"
        ]):
            return {"intent": "sla_breach_deliveries"}
        
        days_match = re.search(r'older than (\d+) days?', question_lower)
        if days_match:
            days = int(days_match.group(1))
            return {"intent": "pending_older_than", "days": days}
        
        # ==================================================
        # HIGHEST PENDING QUERIES
        # ==================================================
        
        if any(phrase in question_lower for phrase in [
            "highest pending dealer", "dealer has highest pending", "top pending dealer",
            "which dealer has the highest pending", "dealer with most pending"
        ]):
            return {"intent": "highest_pending_dealer"}
        
        if any(phrase in question_lower for phrase in [
            "highest pending warehouse", "warehouse has highest pending", "top pending warehouse"
        ]):
            top_match = re.search(r'top\s+(\d+)', question_lower)
            if top_match:
                return {"intent": "top_pending_warehouses", "limit": int(top_match.group(1))}
            return {"intent": "highest_pending_warehouse"}
        
        if any(phrase in question_lower for phrase in [
            "highest pending city", "city has highest pending", "top pending city"
        ]):
            top_match = re.search(r'top\s+(\d+)', question_lower)
            if top_match:
                return {"intent": "top_pending_cities", "limit": int(top_match.group(1))}
            return {"intent": "highest_pending_city"}
        
        # ==================================================
        # POD PENDING QUERIES
        # ==================================================
        
        if any(phrase in question_lower for phrase in [
            "which dealer has the most pod pending", "dealer pod pending highest",
            "dealer with pending pod", "top dealer pod pending"
        ]):
            return {"intent": "highest_pod_pending_dealer"}
        
        if any(phrase in question_lower for phrase in [
            "which warehouse has the most pod pending", "warehouse pod pending highest",
            "top warehouse pod pending"
        ]):
            return {"intent": "highest_pod_pending_warehouse"}
        
        # ==================================================
        # DELIVERY STATUS QUERIES
        # ==================================================
        
        if any(phrase in question_lower for phrase in [
            "how many deliveries are completed", "completed deliveries count",
            "total completed deliveries", "deliveries completed"
        ]):
            return {"intent": "completed_deliveries_count"}
        
        if any(phrase in question_lower for phrase in [
            "how many deliveries are acknowledged", "acknowledged deliveries",
            "pod received count", "deliveries with pod"
        ]):
            return {"intent": "acknowledged_deliveries"}
        
        if any(phrase in question_lower for phrase in [
            "delivered but not acknowledged", "awaiting dealer acknowledgement",
            "delivered pod pending", "pending pod", "awaiting acknowledgement"
        ]):
            return {"intent": "delivered_not_acknowledged"}
        
        # ==================================================
        # PENDING DELIVERIES
        # ==================================================
        
        pending_keywords = ["pending delivery", "pending deliveries", "how many pending", "pending orders"]
        if any(keyword in question_lower for keyword in pending_keywords):
            return {"intent": "pending_deliveries"}
        
        # ==================================================
        # CITY SEARCH
        # ==================================================
        
        city_patterns = [
            r'in\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
            r'for\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
            r'at\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
            r'deliveries?\s+in\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)'
        ]
        
        for pattern in city_patterns:
            city_match = re.search(pattern, question_lower)
            if city_match:
                city = city_match.group(1).strip().title()
                if city and len(city) > 2:
                    return {"intent": "city_search", "city": city}
        
        # ==================================================
        # MATERIAL / PRODUCT SEARCH
        # ==================================================
        
        material_patterns = [
            r'material[\s#:]*([a-zA-Z0-9]+)',
            r'product[\s#:]*([a-zA-Z0-9]+)',
            r'model[\s#:]*([a-zA-Z0-9]+)'
        ]
        
        for pattern in material_patterns:
            material_match = re.search(pattern, question_lower)
            if material_match:
                return {"intent": "material_search", "material_no": material_match.group(1).upper()}
        
        # ==================================================
        # WAREHOUSE SEARCH
        # ==================================================
        
        warehouse_keywords = ["warehouse", "stock from", "from warehouse", "godown"]
        for keyword in warehouse_keywords:
            if keyword in question_lower:
                parts = question_lower.split(keyword)
                if len(parts) > 1:
                    warehouse = parts[1].strip().upper()
                    if warehouse and len(warehouse) >= 2:
                        return {"intent": "warehouse_search", "warehouse": warehouse}
        
        # ==================================================
        # EXECUTIVE / SUMMARY QUERIES
        # ==================================================
        
        executive_keywords = ["ceo", "executive", "performance", "report", "logistics summary", "dashboard overview"]
        if any(keyword in question_lower for keyword in executive_keywords):
            return {"intent": "executive_summary"}
        
        summary_keywords = ["summary", "dashboard", "overview", "statistics", "insights", "analytics"]
        if any(keyword in question_lower for keyword in summary_keywords):
            return {"intent": "dashboard_summary"}
        
        # ==================================================
        # TOP DEALERS / CITIES
        # ==================================================
        
        if "top" in question_lower:
            if "dealer" in question_lower or "customer" in question_lower:
                top_match = re.search(r'top\s+(\d+)', question_lower)
                return {"intent": "top_dealers", "limit": int(top_match.group(1)) if top_match else 10}
            if "city" in question_lower:
                top_match = re.search(r'top\s+(\d+)', question_lower)
                return {"intent": "top_cities", "limit": int(top_match.group(1)) if top_match else 10}
        
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
        """Get product breakdown for a single DN."""
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
        age_days = LogisticsQueryService._calculate_dn_age(main)
        
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
            "age_days": age_days,
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
        
        # NEW: Master dealer dashboard routing
        if intent == "dealer_dashboard":
            dealer_name = intent_result.get("dealer_name", "")
            page = intent_result.get("page", 1)
            result = LogisticsQueryService.get_dealer_complete_dashboard(db, dealer_name, page)
            result["summary"] = result.get("formatted_message", "Dealer dashboard generated.")
        
        elif intent == "dealer_pagination":
            # This would need to track user session for pagination
            # For now, return a message asking to re-enter dealer name
            result = {
                "success": True,
                "summary": "Please re-enter the dealer name to see more pending DNs. Example: 'Show more for FAISAL TRADERS'"
            }
        
        elif intent == "dn_lookup":
            result = LogisticsQueryService.get_dn_product_breakdown(db, intent_result["dn_no"])
            result["summary"] = LogisticsQueryService._format_dn_summary(result)
        
        elif intent == "pending_delivery_aging":
            result = LogisticsQueryService._get_pending_delivery_aging(db)
        
        elif intent == "warehouse_aging":
            result = LogisticsQueryService._get_warehouse_aging(db)
        
        elif intent == "city_aging":
            result = LogisticsQueryService._get_city_aging(db)
        
        elif intent == "critical_pending_dns":
            result = LogisticsQueryService._get_critical_pending_dns(db)
        
        elif intent == "risk_summary":
            result = LogisticsQueryService._generate_risk_summary(db)
        
        elif intent == "top_delayed_dealers":
            result = LogisticsQueryService._get_top_delayed_dealers(db)
        
        elif intent == "sla_breach_deliveries":
            result = LogisticsQueryService._get_sla_breach_deliveries(db)
        
        elif intent == "pending_older_than":
            days = intent_result.get("days", 15)
            result = LogisticsQueryService._get_pending_older_than(db, days)
        
        elif intent == "pending_deliveries":
            result = LogisticsQueryService._get_pending_deliveries(db)
            result["summary"] = f"There are {result.get('count', 0)} pending deliveries totaling {result.get('total_quantity', 0):.0f} units worth Rs {result.get('total_amount', 0):,.2f}."
        
        elif intent == "pending_pod":
            result = LogisticsQueryService._get_pending_pod(db)
            result["summary"] = f"There are {result.get('pending_pod', 0)} deliveries awaiting dealer acknowledgement."
        
        elif intent == "pending_pgi":
            result = LogisticsQueryService._get_pending_pgi(db)
            result["summary"] = f"There are {result.get('pending_pgi', 0)} deliveries pending dispatch from warehouse."
        
        elif intent == "highest_pending_dealer":
            result = LogisticsQueryService._get_highest_pending_dealer(db)
            if result.get("success"):
                result["summary"] = (f"{result['dealer']} currently has the highest pending deliveries "
                                    f"with {result['pending_count']} pending DNs, {result['pending_quantity']:.0f} units, "
                                    f"totaling Rs {result['pending_amount']:,.2f}.")
            else:
                result["summary"] = "No pending deliveries found in the system."
        
        elif intent == "highest_pending_warehouse":
            result = LogisticsQueryService._get_highest_pending_warehouse(db)
            if result.get("success"):
                result["summary"] = (f"Warehouse {result['warehouse']} currently has the highest pending deliveries "
                                    f"with {result['pending_count']} pending DNs, {result['pending_quantity']:.0f} units, "
                                    f"totaling Rs {result['pending_amount']:,.2f}.")
            else:
                result["summary"] = "No pending deliveries found in the system."
        
        elif intent == "top_pending_warehouses":
            limit = intent_result.get("limit", 10)
            result = LogisticsQueryService._get_top_pending_warehouses(db, limit)
            if result.get("success") and result.get("warehouses"):
                warehouse_list = "\n".join([f"{w['rank']}. {w['warehouse']}: {w['pending_dns']} DNs, {w['pending_quantity']:.0f} units" 
                                           for w in result["warehouses"]])
                result["summary"] = f"Top {len(result['warehouses'])} Warehouses by Pending:\n{warehouse_list}"
            else:
                result["summary"] = "No pending deliveries found."
        
        elif intent == "highest_pending_city":
            result = LogisticsQueryService._get_highest_pending_city(db)
            if result.get("success"):
                result["summary"] = (f"{result['city']} currently has the highest pending deliveries "
                                    f"with {result['pending_count']} pending DNs, {result['pending_quantity']:.0f} units, "
                                    f"totaling Rs {result['pending_amount']:,.2f}.")
            else:
                result["summary"] = "No pending deliveries found in the system."
        
        elif intent == "top_pending_cities":
            limit = intent_result.get("limit", 10)
            result = LogisticsQueryService._get_top_pending_cities(db, limit)
            if result.get("success") and result.get("cities"):
                city_list = "\n".join([f"{c['rank']}. {c['city']}: {c['pending_dns']} DNs, {c['pending_quantity']:.0f} units" 
                                      for c in result["cities"]])
                result["summary"] = f"Top {len(result['cities'])} Cities by Pending:\n{city_list}"
            else:
                result["summary"] = "No pending deliveries found."
        
        elif intent == "top_pending_dealers":
            limit = intent_result.get("limit", 10)
            result = LogisticsQueryService._get_top_pending_dealers(db, limit)
            if result.get("success") and result.get("dealers"):
                dealer_list = "\n".join([f"{d['rank']}. {d['dealer']}: {d['pending_dns']} DNs, {d['pending_quantity']:.0f} units" 
                                        for d in result["dealers"]])
                result["summary"] = f"Top {len(result['dealers'])} Dealers by Pending:\n{dealer_list}"
            else:
                result["summary"] = "No pending deliveries found."
        
        elif intent == "highest_pod_pending_dealer":
            result = LogisticsQueryService._get_highest_pod_pending_dealer(db)
            if result.get("success"):
                result["summary"] = (f"{result['dealer']} has the most deliveries awaiting acknowledgement "
                                    f"with {result['pod_pending_count']} DNs, {result['pod_pending_quantity']:.0f} units, "
                                    f"totaling Rs {result['pod_pending_amount']:,.2f}.")
            else:
                result["summary"] = "No POD pending deliveries found."
        
        elif intent == "highest_pod_pending_warehouse":
            result = LogisticsQueryService._get_highest_pod_pending_warehouse(db)
            if result.get("success"):
                result["summary"] = (f"Warehouse {result['warehouse']} has the most deliveries awaiting acknowledgement "
                                    f"with {result['pod_pending_count']} DNs, {result['pod_pending_quantity']:.0f} units, "
                                    f"totaling Rs {result['pod_pending_amount']:,.2f}.")
            else:
                result["summary"] = "No POD pending deliveries found."
        
        elif intent == "completed_deliveries_count":
            result = LogisticsQueryService._get_completed_deliveries_count(db)
            result["summary"] = (f"There are {result['completed_count']} completed deliveries "
                                f"totaling {result['completed_quantity']:.0f} units worth Rs {result['completed_amount']:,.2f}.")
        
        elif intent == "acknowledged_deliveries":
            result = LogisticsQueryService._get_acknowledged_deliveries(db)
            result["summary"] = (f"There are {result['acknowledged_count']} acknowledged deliveries "
                                f"totaling {result['acknowledged_quantity']:.0f} units worth Rs {result['acknowledged_amount']:,.2f}.")
        
        elif intent == "delivered_not_acknowledged":
            result = LogisticsQueryService._get_delivered_not_acknowledged(db)
            result["summary"] = (f"There are {result['delivered_not_acknowledged_count']} deliveries that have been dispatched "
                                f"but are awaiting dealer acknowledgement, totaling {result['delivered_not_acknowledged_quantity']:.0f} units "
                                f"worth Rs {result['delivered_not_acknowledged_amount']:,.2f}.")
        
        elif intent == "city_search":
            result = LogisticsQueryService._get_city_deliveries(db, intent_result["city"])
            result["summary"] = f"Found {result.get('count', 0)} deliveries for {intent_result['city']}."
        
        elif intent == "material_search":
            result = LogisticsQueryService._search_material(db, intent_result["material_no"])
            result["summary"] = f"Material {intent_result['material_no']} appears in {result.get('count', 0)} deliveries with total quantity {result.get('total_quantity', 0):.0f} units."
        
        elif intent == "warehouse_search":
            result = LogisticsQueryService._get_warehouse_deliveries(db, intent_result["warehouse"])
            result["summary"] = f"Warehouse {intent_result['warehouse']} has {result.get('count', 0)} deliveries."
        
        elif intent == "dashboard_summary":
            result = LogisticsQueryService._get_delivery_insights(db)
            result["summary"] = (f"Logistics Summary: {result['total_records']} total DNs. "
                                f"{result['pending_deliveries']} pending (Rs {result['pending_amount']:,.2f}). "
                                f"Top city: {result['top_city']}. Top warehouse: {result['top_warehouse']}.")
        
        elif intent == "executive_summary":
            result = LogisticsQueryService._get_executive_summary(db)
            result["summary"] = result.get("executive_summary", "Executive summary generated successfully.")
        
        elif intent == "top_dealers":
            limit = intent_result.get("limit", 10)
            result = LogisticsQueryService._get_top_dealers(db, limit)
            result["summary"] = f"Top dealers: {', '.join([d['dealer_name'] for d in result['records'][:5]])}"
        
        elif intent == "top_cities":
            limit = intent_result.get("limit", 10)
            result = LogisticsQueryService._get_top_cities(db, limit)
            result["summary"] = f"Top cities by delivery volume: {', '.join([c['city'] for c in result[:5]])}"
        
        else:
            result = LogisticsQueryService._get_delivery_insights(db)
            result["summary"] = "Here's the current logistics dashboard summary."
        
        result["question"] = question
        result["intent"] = intent
        
        return result
    
    @staticmethod
    def _format_dn_summary(dn_result: Dict[str, Any]) -> str:
        """Format DN summary for WhatsApp."""
        if not dn_result.get("success"):
            return f"DN {dn_result.get('dn_no', 'unknown')} was not found in the system."
        
        dn_no = dn_result.get("dn_no", "Unknown")
        dealer = dn_result.get("dealer", "Unknown Customer")
        city = dn_result.get("city", "Unknown City")
        warehouse = dn_result.get("warehouse", "Unknown Warehouse")
        status = dn_result.get("status", "Unknown")
        pod_status = dn_result.get("pod_status", "Pending")
        age_days = dn_result.get("age_days", 0)
        total_amount = dn_result.get("total_amount", 0)
        total_quantity = dn_result.get("total_quantity", 0)
        products = dn_result.get("products", [])
        
        summary = f"🔹 *DN {dn_no}*\n"
        summary += f"📋 Dealer: {dealer}\n"
        summary += f"📍 City: {city}\n"
        summary += f"🏭 Warehouse: {warehouse}\n"
        summary += f"📋 Status: {status}\n"
        summary += f"📋 POD: {pod_status}\n"
        summary += f"📅 Age: {age_days} days\n"
        summary += f"📦 Total Quantity: {total_quantity:.0f} units\n"
        summary += f"💰 Amount: Rs {total_amount:,.2f}\n\n"
        
        if products:
            summary += "📦 *Products:*\n"
            for p in products[:5]:
                summary += f"   • {p['product_name']}: {p['quantity']:.0f} units\n"
            if len(products) > 5:
                summary += f"   • +{len(products) - 5} more products\n"
        
        if age_days > 15:
            summary += "\n⚠️ *CRITICAL:* This delivery requires immediate attention!"
        
        return summary
    
    # ======================================================
    # INTERNAL METHODS (PREVIOUSLY PUBLIC, NOW PRIVATE)
    # These are kept for backward compatibility but new code should use get_dealer_complete_dashboard
    # ======================================================
    
    @staticmethod
    def _get_pending_delivery_aging(db: Session) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "message": "No pending deliveries found"}
        
        aging_groups = {
            "0-3 Days": {"dns": set(), "quantity": 0},
            "4-7 Days": {"dns": set(), "quantity": 0},
            "8-15 Days": {"dns": set(), "quantity": 0},
            "16-30 Days": {"dns": set(), "quantity": 0},
            "30+ Days": {"dns": set(), "quantity": 0}
        }
        
        for r in pending_records:
            age_days = LogisticsQueryService._calculate_dn_age(r)
            
            if age_days <= 3:
                aging_groups["0-3 Days"]["dns"].add(r.dn_no)
                aging_groups["0-3 Days"]["quantity"] += float(r.dn_qty or 0)
            elif age_days <= 7:
                aging_groups["4-7 Days"]["dns"].add(r.dn_no)
                aging_groups["4-7 Days"]["quantity"] += float(r.dn_qty or 0)
            elif age_days <= 15:
                aging_groups["8-15 Days"]["dns"].add(r.dn_no)
                aging_groups["8-15 Days"]["quantity"] += float(r.dn_qty or 0)
            elif age_days <= 30:
                aging_groups["16-30 Days"]["dns"].add(r.dn_no)
                aging_groups["16-30 Days"]["quantity"] += float(r.dn_qty or 0)
            else:
                aging_groups["30+ Days"]["dns"].add(r.dn_no)
                aging_groups["30+ Days"]["quantity"] += float(r.dn_qty or 0)
        
        total_pending = sum(len(g["dns"]) for g in aging_groups.values())
        
        summary = f"📊 PENDING DELIVERY AGING REPORT\n\n"
        summary += f"Total Pending DNs: {total_pending}\n\n"
        for category, data in aging_groups.items():
            if len(data["dns"]) > 0:
                summary += f"• {category}: {len(data['dns'])} DNs, {data['quantity']:.0f} units\n"
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _get_warehouse_aging(db: Session, limit: int = 10) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "warehouses": [], "count": 0}
        
        warehouse_stats = {}
        for r in pending_records:
            if not r.warehouse:
                continue
            if r.warehouse not in warehouse_stats:
                warehouse_stats[r.warehouse] = {"dns": set(), "max_age": 0, "quantity": 0}
            
            age = LogisticsQueryService._calculate_dn_age(r)
            warehouse_stats[r.warehouse]["dns"].add(r.dn_no)
            warehouse_stats[r.warehouse]["quantity"] += float(r.dn_qty or 0)
            
            if age > warehouse_stats[r.warehouse]["max_age"]:
                warehouse_stats[r.warehouse]["max_age"] = age
        
        sorted_warehouses = sorted(warehouse_stats.items(), key=lambda x: x[1]["max_age"], reverse=True)[:limit]
        
        summary = "🏭 WAREHOUSE AGING RANKING\n\n"
        for i, w in enumerate(sorted_warehouses, 1):
            summary += f"{i}. {w[0]}\n"
            summary += f"   Pending DNs: {len(w[1]['dns'])}, Pending Units: {w[1]['quantity']:.0f}\n"
            summary += f"   Oldest Age: {w[1]['max_age']} days\n\n"
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _get_city_aging(db: Session, limit: int = 10) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "cities": [], "count": 0}
        
        city_stats = {}
        for r in pending_records:
            if not r.ship_to_city:
                continue
            if r.ship_to_city not in city_stats:
                city_stats[r.ship_to_city] = {"dns": set(), "max_age": 0, "quantity": 0}
            
            age = LogisticsQueryService._calculate_dn_age(r)
            city_stats[r.ship_to_city]["dns"].add(r.dn_no)
            city_stats[r.ship_to_city]["quantity"] += float(r.dn_qty or 0)
            
            if age > city_stats[r.ship_to_city]["max_age"]:
                city_stats[r.ship_to_city]["max_age"] = age
        
        sorted_cities = sorted(city_stats.items(), key=lambda x: x[1]["max_age"], reverse=True)[:limit]
        
        summary = "🌆 CITY AGING RANKING\n\n"
        for i, c in enumerate(sorted_cities, 1):
            summary += f"{i}. {c[0]}\n"
            summary += f"   Pending DNs: {len(c[1]['dns'])}, Pending Units: {c[1]['quantity']:.0f}\n"
            summary += f"   Oldest Age: {c[1]['max_age']} days\n\n"
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _get_critical_pending_dns(db: Session, threshold_days: int = 15, limit: int = 20) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        critical_dns = []
        seen_dns = set()
        
        for r in pending_records:
            age_days = LogisticsQueryService._calculate_dn_age(r)
            if age_days >= threshold_days and r.dn_no not in seen_dns:
                seen_dns.add(r.dn_no)
                critical_dns.append({
                    "dn_no": r.dn_no,
                    "dealer": r.customer_name,
                    "age_days": age_days,
                    "quantity": float(r.dn_qty or 0)
                })
        
        critical_dns.sort(key=lambda x: x["age_days"], reverse=True)
        
        summary = f"🚨 CRITICAL DELIVERIES ({threshold_days}+ days)\n\n"
        for dn in critical_dns[:limit]:
            summary += f"• DN {dn['dn_no']}: {dn['dealer']}\n"
            summary += f"   Age: {dn['age_days']} days, Qty: {dn['quantity']:.0f} units\n"
        
        if len(critical_dns) > limit:
            summary += f"\nAnd {len(critical_dns) - limit} more critical deliveries"
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _generate_risk_summary(db: Session) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        age_counts = {">15": 0, ">30": 0}
        oldest_age = 0
        oldest_dn = None
        oldest_dealer = None
        
        for r in pending_records:
            age = LogisticsQueryService._calculate_dn_age(r)
            if age > 15:
                age_counts[">15"] += 1
            if age > 30:
                age_counts[">30"] += 1
            if age > oldest_age:
                oldest_age = age
                oldest_dn = r.dn_no
                oldest_dealer = r.customer_name
        
        summary = f"""🚨 EXECUTIVE RISK SUMMARY

📊 DELAY STATISTICS:
• {age_counts['>15']} DNs older than 15 days
• {age_counts['>30']} DNs older than 30 days
• Oldest DN: {oldest_dn} ({oldest_age} days) - {oldest_dealer}

🎯 RECOMMENDATIONS:
• Prioritize clearance of DNs over 15 days
• Follow up with {oldest_dealer} for oldest pending
• Focus on reducing backlog"""
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _get_top_delayed_dealers(db: Session, limit: int = 10) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        dealer_stats = {}
        for r in pending_records:
            age_days = LogisticsQueryService._calculate_dn_age(r)
            if age_days >= 15 and r.customer_name:
                if r.customer_name not in dealer_stats:
                    dealer_stats[r.customer_name] = {"dns": set(), "quantity": 0, "max_age": 0}
                dealer_stats[r.customer_name]["dns"].add(r.dn_no)
                dealer_stats[r.customer_name]["quantity"] += float(r.dn_qty or 0)
                if age_days > dealer_stats[r.customer_name]["max_age"]:
                    dealer_stats[r.customer_name]["max_age"] = age_days
        
        sorted_dealers = sorted(dealer_stats.items(), key=lambda x: len(x[1]["dns"]), reverse=True)[:limit]
        
        summary = "🏆 TOP DELAYED DEALERS (15+ days)\n\n"
        for i, d in enumerate(sorted_dealers, 1):
            summary += f"{i}. {d[0]}: {len(d[1]['dns'])} DNs, {d[1]['quantity']:.0f} units\n"
            summary += f"   Max Delay: {d[1]['max_age']} days\n\n"
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _get_sla_breach_deliveries(db: Session) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        sla_breaches = {"Green (0-3 Days)": 0, "Yellow (4-7 Days)": 0, "Orange (8-15 Days)": 0, "Red (15+ Days)": 0}
        red_alerts = []
        
        for r in pending_records:
            age_days = LogisticsQueryService._calculate_dn_age(r)
            
            if age_days <= 3:
                sla_breaches["Green (0-3 Days)"] += 1
            elif age_days <= 7:
                sla_breaches["Yellow (4-7 Days)"] += 1
            elif age_days <= 15:
                sla_breaches["Orange (8-15 Days)"] += 1
            else:
                sla_breaches["Red (15+ Days)"] += 1
                if len(red_alerts) < 10:
                    red_alerts.append({"dn_no": r.dn_no, "dealer": r.customer_name, "age_days": age_days})
        
        summary = f"""🔴 SLA BREACH DELIVERIES

• Green (0-3 days): {sla_breaches['Green (0-3 Days)']} DNs
• Yellow (4-7 days): {sla_breaches['Yellow (4-7 Days)']} DNs
• Orange (8-15 days): {sla_breaches['Orange (8-15 Days)']} DNs
• Red (15+ days): {sla_breaches['Red (15+ Days)']} DNs

⚠️ RED ALERT (15+ days):
"""
        for dn in red_alerts:
            summary += f"   • DN {dn['dn_no']}: {dn['dealer']} ({dn['age_days']} days)\n"
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _get_pending_older_than(db: Session, days: int) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        older_than = []
        seen_dns = set()
        
        for r in pending_records:
            age_days = LogisticsQueryService._calculate_dn_age(r)
            if age_days > days and r.dn_no not in seen_dns:
                seen_dns.add(r.dn_no)
                older_than.append({
                    "dn_no": r.dn_no,
                    "dealer": r.customer_name,
                    "age_days": age_days,
                    "quantity": float(r.dn_qty or 0)
                })
        
        older_than.sort(key=lambda x: x["age_days"], reverse=True)
        
        summary = f"📋 PENDING DELIVERIES OLDER THAN {days} DAYS\n\n"
        for dn in older_than[:15]:
            summary += f"• DN {dn['dn_no']}: {dn['dealer']} - {dn['age_days']} days\n"
            summary += f"   Quantity: {dn['quantity']:.0f} units\n"
        
        if len(older_than) > 15:
            summary += f"\nAnd {len(older_than) - 15} more deliveries"
        
        return {"success": True, "summary": summary}
    
    @staticmethod
    def _get_pending_deliveries(db: Session, limit: int = 100):
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
    
    @staticmethod
    def _get_pending_pod(db: Session):
        rows = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending",
            DeliveryReport.pgi_status == "Completed"
        ).all()
        unique_dns = LogisticsQueryService._get_unique_dns(rows)
        return {"success": True, "pending_pod": len(unique_dns)}
    
    @staticmethod
    def _get_pending_pgi(db: Session):
        rows = db.query(DeliveryReport).filter(DeliveryReport.pgi_status == "Pending").all()
        unique_dns = LogisticsQueryService._get_unique_dns(rows)
        return {"success": True, "pending_pgi": len(unique_dns)}
    
    @staticmethod
    def _get_highest_pending_dealer(db: Session) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": False, "message": "No pending deliveries found"}
        
        dealer_stats = {}
        for r in pending_records:
            if not r.customer_name:
                continue
            if r.customer_name not in dealer_stats:
                dealer_stats[r.customer_name] = {"dns": set(), "amount": 0, "quantity": 0}
            dealer_stats[r.customer_name]["dns"].add(r.dn_no)
            dealer_stats[r.customer_name]["amount"] += float(r.dn_amount or 0)
            dealer_stats[r.customer_name]["quantity"] += float(r.dn_qty or 0)
        
        if not dealer_stats:
            return {"success": False, "message": "No pending deliveries found"}
        
        top_dealer = max(dealer_stats.items(), key=lambda x: len(x[1]["dns"]))
        
        return {
            "success": True,
            "dealer": top_dealer[0],
            "pending_count": len(top_dealer[1]["dns"]),
            "pending_amount": top_dealer[1]["amount"],
            "pending_quantity": top_dealer[1]["quantity"]
        }
    
    @staticmethod
    def _get_highest_pending_warehouse(db: Session) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": False, "message": "No pending deliveries found"}
        
        warehouse_stats = {}
        for r in pending_records:
            if not r.warehouse:
                continue
            if r.warehouse not in warehouse_stats:
                warehouse_stats[r.warehouse] = {"dns": set(), "amount": 0, "quantity": 0}
            warehouse_stats[r.warehouse]["dns"].add(r.dn_no)
            warehouse_stats[r.warehouse]["amount"] += float(r.dn_amount or 0)
            warehouse_stats[r.warehouse]["quantity"] += float(r.dn_qty or 0)
        
        if not warehouse_stats:
            return {"success": False, "message": "No pending deliveries found"}
        
        top_warehouse = max(warehouse_stats.items(), key=lambda x: len(x[1]["dns"]))
        
        return {
            "success": True,
            "warehouse": top_warehouse[0],
            "pending_count": len(top_warehouse[1]["dns"]),
            "pending_amount": top_warehouse[1]["amount"],
            "pending_quantity": top_warehouse[1]["quantity"]
        }
    
    @staticmethod
    def _get_top_pending_warehouses(db: Session, limit: int = 10) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "warehouses": [], "count": 0}
        
        warehouse_stats = {}
        for r in pending_records:
            if not r.warehouse:
                continue
            if r.warehouse not in warehouse_stats:
                warehouse_stats[r.warehouse] = {"dns": set(), "amount": 0, "quantity": 0}
            warehouse_stats[r.warehouse]["dns"].add(r.dn_no)
            warehouse_stats[r.warehouse]["amount"] += float(r.dn_amount or 0)
            warehouse_stats[r.warehouse]["quantity"] += float(r.dn_qty or 0)
        
        sorted_warehouses = sorted(warehouse_stats.items(), key=lambda x: len(x[1]["dns"]), reverse=True)[:limit]
        
        warehouses = [
            {
                "rank": i + 1,
                "warehouse": w[0],
                "pending_dns": len(w[1]["dns"]),
                "pending_quantity": w[1]["quantity"],
                "pending_amount": w[1]["amount"]
            }
            for i, w in enumerate(sorted_warehouses)
        ]
        
        return {"success": True, "warehouses": warehouses, "count": len(warehouses)}
    
    @staticmethod
    def _get_highest_pending_city(db: Session) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": False, "message": "No pending deliveries found"}
        
        city_stats = {}
        for r in pending_records:
            if not r.ship_to_city:
                continue
            if r.ship_to_city not in city_stats:
                city_stats[r.ship_to_city] = {"dns": set(), "amount": 0, "quantity": 0}
            city_stats[r.ship_to_city]["dns"].add(r.dn_no)
            city_stats[r.ship_to_city]["amount"] += float(r.dn_amount or 0)
            city_stats[r.ship_to_city]["quantity"] += float(r.dn_qty or 0)
        
        if not city_stats:
            return {"success": False, "message": "No pending deliveries found"}
        
        top_city = max(city_stats.items(), key=lambda x: len(x[1]["dns"]))
        
        return {
            "success": True,
            "city": top_city[0],
            "pending_count": len(top_city[1]["dns"]),
            "pending_amount": top_city[1]["amount"],
            "pending_quantity": top_city[1]["quantity"]
        }
    
    @staticmethod
    def _get_top_pending_cities(db: Session, limit: int = 10) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "cities": [], "count": 0}
        
        city_stats = {}
        for r in pending_records:
            if not r.ship_to_city:
                continue
            if r.ship_to_city not in city_stats:
                city_stats[r.ship_to_city] = {"dns": set(), "amount": 0, "quantity": 0}
            city_stats[r.ship_to_city]["dns"].add(r.dn_no)
            city_stats[r.ship_to_city]["amount"] += float(r.dn_amount or 0)
            city_stats[r.ship_to_city]["quantity"] += float(r.dn_qty or 0)
        
        sorted_cities = sorted(city_stats.items(), key=lambda x: len(x[1]["dns"]), reverse=True)[:limit]
        
        cities = [
            {
                "rank": i + 1,
                "city": c[0],
                "pending_dns": len(c[1]["dns"]),
                "pending_quantity": c[1]["quantity"],
                "pending_amount": c[1]["amount"]
            }
            for i, c in enumerate(sorted_cities)
        ]
        
        return {"success": True, "cities": cities, "count": len(cities)}
    
    @staticmethod
    def _get_top_pending_dealers(db: Session, limit: int = 10) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "dealers": [], "count": 0}
        
        dealer_stats = {}
        for r in pending_records:
            if not r.customer_name:
                continue
            if r.customer_name not in dealer_stats:
                dealer_stats[r.customer_name] = {"dns": set(), "amount": 0, "quantity": 0}
            dealer_stats[r.customer_name]["dns"].add(r.dn_no)
            dealer_stats[r.customer_name]["amount"] += float(r.dn_amount or 0)
            dealer_stats[r.customer_name]["quantity"] += float(r.dn_qty or 0)
        
        sorted_dealers = sorted(dealer_stats.items(), key=lambda x: len(x[1]["dns"]), reverse=True)[:limit]
        
        dealers = [
            {
                "rank": i + 1,
                "dealer": d[0],
                "pending_dns": len(d[1]["dns"]),
                "pending_quantity": d[1]["quantity"],
                "pending_amount": d[1]["amount"]
            }
            for i, d in enumerate(sorted_dealers)
        ]
        
        return {"success": True, "dealers": dealers, "count": len(dealers)}
    
    @staticmethod
    def _get_highest_pod_pending_dealer(db: Session) -> Dict[str, Any]:
        pod_pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending",
            DeliveryReport.pgi_status == "Completed"
        ).all()
        
        if not pod_pending_records:
            return {"success": False, "message": "No POD pending deliveries found"}
        
        dealer_stats = {}
        for r in pod_pending_records:
            if not r.customer_name:
                continue
            if r.customer_name not in dealer_stats:
                dealer_stats[r.customer_name] = {"dns": set(), "amount": 0, "quantity": 0}
            dealer_stats[r.customer_name]["dns"].add(r.dn_no)
            dealer_stats[r.customer_name]["amount"] += float(r.dn_amount or 0)
            dealer_stats[r.customer_name]["quantity"] += float(r.dn_qty or 0)
        
        top_dealer = max(dealer_stats.items(), key=lambda x: len(x[1]["dns"]))
        
        return {
            "success": True,
            "dealer": top_dealer[0],
            "pod_pending_count": len(top_dealer[1]["dns"]),
            "pod_pending_amount": top_dealer[1]["amount"],
            "pod_pending_quantity": top_dealer[1]["quantity"]
        }
    
    @staticmethod
    def _get_highest_pod_pending_warehouse(db: Session) -> Dict[str, Any]:
        pod_pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending",
            DeliveryReport.pgi_status == "Completed"
        ).all()
        
        if not pod_pending_records:
            return {"success": False, "message": "No POD pending deliveries found"}
        
        warehouse_stats = {}
        for r in pod_pending_records:
            if not r.warehouse:
                continue
            if r.warehouse not in warehouse_stats:
                warehouse_stats[r.warehouse] = {"dns": set(), "amount": 0, "quantity": 0}
            warehouse_stats[r.warehouse]["dns"].add(r.dn_no)
            warehouse_stats[r.warehouse]["amount"] += float(r.dn_amount or 0)
            warehouse_stats[r.warehouse]["quantity"] += float(r.dn_qty or 0)
        
        top_warehouse = max(warehouse_stats.items(), key=lambda x: len(x[1]["dns"]))
        
        return {
            "success": True,
            "warehouse": top_warehouse[0],
            "pod_pending_count": len(top_warehouse[1]["dns"]),
            "pod_pending_amount": top_warehouse[1]["amount"],
            "pod_pending_quantity": top_warehouse[1]["quantity"]
        }
    
    @staticmethod
    def _get_completed_deliveries_count(db: Session) -> Dict[str, Any]:
        completed_records = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Received"
        ).all()
        
        unique_dns = LogisticsQueryService._get_unique_dns(completed_records)
        total_amount = sum(r.dn_amount or 0 for r in completed_records)
        total_quantity = sum(r.dn_qty or 0 for r in completed_records)
        
        return {
            "success": True,
            "completed_count": len(unique_dns),
            "completed_amount": float(total_amount),
            "completed_quantity": float(total_quantity)
        }
    
    @staticmethod
    def _get_acknowledged_deliveries(db: Session) -> Dict[str, Any]:
        rows = db.query(DeliveryReport).filter(DeliveryReport.pod_status == "Received").all()
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        unique_dns = LogisticsQueryService._get_unique_dns(rows)
        total_amount = sum(r.get("dn_amount", 0) for r in records)
        total_quantity = sum(r.get("dn_qty", 0) for r in records)
        
        return {
            "success": True,
            "acknowledged_count": len(unique_dns),
            "acknowledged_amount": float(total_amount),
            "acknowledged_quantity": float(total_quantity)
        }
    
    @staticmethod
    def _get_delivered_not_acknowledged(db: Session) -> Dict[str, Any]:
        rows = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending"
        ).all()
        
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        unique_dns = LogisticsQueryService._get_unique_dns(rows)
        total_amount = sum(r.get("dn_amount", 0) for r in records)
        total_quantity = sum(r.get("dn_qty", 0) for r in records)
        
        return {
            "success": True,
            "delivered_not_acknowledged_count": len(unique_dns),
            "delivered_not_acknowledged_amount": float(total_amount),
            "delivered_not_acknowledged_quantity": float(total_quantity)
        }
    
    @staticmethod
    def _get_city_deliveries(db: Session, city: str, limit: int = 100):
        rows = db.query(DeliveryReport).filter(DeliveryReport.ship_to_city.ilike(f"%{city}%")).all()
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        return {"success": True, "city": city, "count": len(records), "records": records}
    
    @staticmethod
    def _get_warehouse_deliveries(db: Session, warehouse: str, limit: int = 100):
        rows = db.query(DeliveryReport).filter(DeliveryReport.warehouse.ilike(f"%{warehouse}%")).all()
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        return {"success": True, "warehouse": warehouse, "count": len(records), "records": records}
    
    @staticmethod
    def _search_material(db: Session, material_no: str, limit: int = 50) -> Dict[str, Any]:
        rows = db.query(DeliveryReport).filter(DeliveryReport.material_no.ilike(f"%{material_no}%")).all()
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        total_qty = sum(r.get("dn_qty", 0) for r in records)
        
        return {
            "success": True,
            "material_no": material_no,
            "count": len(records),
            "total_quantity": float(total_qty),
            "records": records
        }
    
    @staticmethod
    def _get_top_dealers(db: Session, limit: int = 10) -> Dict[str, Any]:
        rows = db.query(DeliveryReport).all()
        
        dealer_stats = {}
        for r in rows:
            if r.customer_name:
                if r.customer_name not in dealer_stats:
                    dealer_stats[r.customer_name] = {"dns": set(), "amount": 0, "quantity": 0}
                dealer_stats[r.customer_name]["dns"].add(r.dn_no)
                dealer_stats[r.customer_name]["amount"] += float(r.dn_amount or 0)
                dealer_stats[r.customer_name]["quantity"] += float(r.dn_qty or 0)
        
        sorted_dealers = sorted(dealer_stats.items(), key=lambda x: len(x[1]["dns"]), reverse=True)[:limit]
        
        records = [
            {
                "dealer_name": d[0],
                "delivery_count": len(d[1]["dns"]),
                "total_amount": d[1]["amount"],
                "total_quantity": d[1]["quantity"]
            }
            for d in sorted_dealers
        ]
        
        return {"success": True, "count": len(records), "records": records}
    
    @staticmethod
    def _get_top_cities(db: Session, limit: int = 10):
        rows = db.query(DeliveryReport).all()
        
        city_stats = {}
        for r in rows:
            if r.ship_to_city:
                if r.ship_to_city not in city_stats:
                    city_stats[r.ship_to_city] = {"dns": set(), "amount": 0, "quantity": 0}
                city_stats[r.ship_to_city]["dns"].add(r.dn_no)
                city_stats[r.ship_to_city]["amount"] += float(r.dn_amount or 0)
                city_stats[r.ship_to_city]["quantity"] += float(r.dn_qty or 0)
        
        sorted_cities = sorted(city_stats.items(), key=lambda x: len(x[1]["dns"]), reverse=True)[:limit]
        
        return [
            {
                "city": c[0],
                "count": len(c[1]["dns"]),
                "total_amount": c[1]["amount"],
                "total_quantity": c[1]["quantity"]
            }
            for c in sorted_cities if c[0]
        ]
    
    @staticmethod
    def _get_delivery_insights(db: Session) -> Dict[str, Any]:
        all_records = db.query(DeliveryReport).all()
        unique_dns = LogisticsQueryService._get_unique_dns(all_records)
        
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        pending_unique_dns = LogisticsQueryService._get_unique_dns(pending_records)
        
        completed_records = [r for r in all_records if r.pgi_status == "Completed" and r.pod_status == "Received"]
        completed_unique_dns = LogisticsQueryService._get_unique_dns(completed_records)
        
        delivered_not_ack_records = [r for r in all_records if r.pgi_status == "Completed" and r.pod_status == "Pending"]
        delivered_not_ack_unique_dns = LogisticsQueryService._get_unique_dns(delivered_not_ack_records)
        
        pending_amount = sum(r.dn_amount or 0 for r in pending_records)
        
        warehouse_stats = {}
        for r in all_records:
            if r.warehouse:
                if r.warehouse not in warehouse_stats:
                    warehouse_stats[r.warehouse] = set()
                warehouse_stats[r.warehouse].add(r.dn_no)
        
        top_warehouse = max(warehouse_stats.items(), key=lambda x: len(x[1]))[0] if warehouse_stats else "N/A"
        
        city_stats = {}
        for r in all_records:
            if r.ship_to_city:
                if r.ship_to_city not in city_stats:
                    city_stats[r.ship_to_city] = set()
                city_stats[r.ship_to_city].add(r.dn_no)
        
        top_city = max(city_stats.items(), key=lambda x: len(x[1]))[0] if city_stats else "N/A"
        
        avg_amount = sum(r.dn_amount or 0 for r in all_records) / len(unique_dns) if unique_dns else 0
        
        return {
            "success": True,
            "total_records": len(unique_dns),
            "pending_deliveries": len(pending_unique_dns),
            "completed_deliveries": len(completed_unique_dns),
            "delivered_not_acknowledged": len(delivered_not_ack_unique_dns),
            "pending_amount": float(pending_amount),
            "top_warehouse": top_warehouse,
            "top_city": top_city,
            "average_dn_amount": float(avg_amount)
        }
    
    @staticmethod
    def _get_executive_summary(db: Session) -> Dict[str, Any]:
        insights = LogisticsQueryService._get_delivery_insights(db)
        top_dealers = LogisticsQueryService._get_top_dealers(db, 5)
        top_pending_warehouses = LogisticsQueryService._get_top_pending_warehouses(db, 5)
        highest_pending_dealer = LogisticsQueryService._get_highest_pending_dealer(db)
        
        completion_rate = round((insights["completed_deliveries"] / insights["total_records"] * 100) 
                                if insights["total_records"] > 0 else 0, 2)
        
        executive_summary = f"""📊 EXECUTIVE LOGISTICS REPORT

📈 OVERVIEW:
• Total DNs: {insights['total_records']}
• Completion Rate: {completion_rate}%
• Avg Order Value: Rs {insights['average_dn_amount']:,.2f}

⏳ PENDING STATUS:
• Pending DNs: {insights['pending_deliveries']} (Rs {insights['pending_amount']:,.2f})
• Delivered - Awaiting Acknowledgment: {insights['delivered_not_acknowledged']}

🏆 HIGHEST PENDING:
• Dealer: {highest_pending_dealer.get('dealer', 'N/A')} ({highest_pending_dealer.get('pending_count', 0)} DNs)

🎯 RECOMMENDATIONS:
• Focus on clearing pending dispatches
• Follow up with {highest_pending_dealer.get('dealer', 'dealers')} for pending acknowledgements"""
        
        return {
            "success": True,
            "executive_summary": executive_summary,
            **insights,
            "top_dealers": top_dealers["records"],
            "top_pending_warehouses": top_pending_warehouses.get("warehouses", []),
            "highest_pending_dealer": highest_pending_dealer
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
- DN Age > 15 days = Critical
- DN Age > 30 days = Urgent

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
