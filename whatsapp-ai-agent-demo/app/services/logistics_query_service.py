# ==========================================================
# FILE: app/services/logistics_query_service.py
# ==========================================================

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import Dict, List, Any, Optional, Tuple
import re
from datetime import datetime, date

from app.models import DeliveryReport


class LogisticsQueryService:

    # ======================================================
    # HELPER: Unique DN counting
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
            "updated_at": record.updated_at.isoformat() if record.updated_at else None
        }
    
    # ======================================================
    # INTENT DETECTION (UPDATED)
    # ======================================================
    
    @staticmethod
    def detect_intent(question: str) -> Dict[str, Any]:
        """Detect user intent from natural language question."""
        question_lower = question.lower().strip()
        
        # DN NUMBER DETECTION
        dn_match = re.search(r'\b(\d{8,15})\b', question)
        if dn_match:
            return {"intent": "dn_lookup", "dn_no": dn_match.group(1)}
        
        dn_keywords = ["dn", "delivery note", "delivery number", "status", "check", "show"]
        for keyword in dn_keywords:
            if keyword in question_lower:
                dn_match_with_keyword = re.search(r'\b(\d{8,15})\b', question)
                if dn_match_with_keyword:
                    return {"intent": "dn_lookup", "dn_no": dn_match_with_keyword.group(1)}
        
        # DEALER LOOKUP
        dealer_keywords = ["dealer", "customer", "show me deliveries for", "deliveries of", "tell me about"]
        for keyword in dealer_keywords:
            if keyword in question_lower:
                parts = question_lower.split(keyword)
                if len(parts) > 1:
                    dealer_name = parts[1].strip().title()
                    if dealer_name and len(dealer_name) > 2:
                        return {"intent": "dealer_lookup", "dealer_name": dealer_name}
        
        # Dealer DN breakdown
        if any(phrase in question_lower for phrase in ["dn for", "delivery notes for", "show dns for"]):
            dealer_match = re.search(r'(?:for|of|from)\s+([a-zA-Z\s]+)', question_lower)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip().title()
                if dealer_name and len(dealer_name) > 2:
                    return {"intent": "dealer_dn_breakdown", "dealer_name": dealer_name}
        
        # Dealer products
        if any(phrase in question_lower for phrase in ["products for", "what products", "items for", "product breakdown"]):
            dealer_match = re.search(r'(?:for|of|from)\s+([a-zA-Z\s]+)', question_lower)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip().title()
                if dealer_name and len(dealer_name) > 2:
                    return {"intent": "dealer_products", "dealer_name": dealer_name}
        
        # HIGHEST PENDING QUERIES
        if any(phrase in question_lower for phrase in [
            "highest pending dealer", "dealer has highest pending", "top pending dealer",
            "which dealer has the highest pending", "dealer with most pending"
        ]):
            return {"intent": "highest_pending_dealer"}
        
        if any(phrase in question_lower for phrase in [
            "highest pending warehouse", "warehouse has highest pending", "top pending warehouse",
            "which warehouse has the highest pending", "warehouse with most pending", "top 5 warehouses"
        ]):
            # Check for top N
            top_match = re.search(r'top\s+(\d+)', question_lower)
            if top_match:
                return {"intent": "top_pending_warehouses", "limit": int(top_match.group(1))}
            return {"intent": "highest_pending_warehouse"}
        
        if any(phrase in question_lower for phrase in [
            "highest pending city", "city has highest pending", "top pending city",
            "which city has the highest pending", "city with most pending", "top 10 cities"
        ]):
            top_match = re.search(r'top\s+(\d+)', question_lower)
            if top_match:
                return {"intent": "top_pending_cities", "limit": int(top_match.group(1))}
            return {"intent": "highest_pending_city"}
        
        # Top pending dealers ranking
        if any(phrase in question_lower for phrase in ["top pending dealers", "dealers with most pending", "top 10 dealers"]):
            top_match = re.search(r'top\s+(\d+)', question_lower)
            return {"intent": "top_pending_dealers", "limit": int(top_match.group(1)) if top_match else 10}
        
        # Pending units by warehouse
        if any(phrase in question_lower for phrase in ["pending units by warehouse", "warehouse wise pending", "pending quantity per warehouse"]):
            return {"intent": "pending_units_by_warehouse"}
        
        # Pending units by city
        if any(phrase in question_lower for phrase in ["pending units by city", "city wise pending", "pending quantity per city"]):
            return {"intent": "pending_units_by_city"}
        
        # POD PENDING QUERIES
        if any(phrase in question_lower for phrase in [
            "which dealer has the most pod pending", "dealer pod pending highest", "top dealer pod pending"
        ]):
            return {"intent": "highest_pod_pending_dealer"}
        
        if any(phrase in question_lower for phrase in [
            "which warehouse has the most pod pending", "warehouse pod pending highest", "top warehouse pod pending"
        ]):
            return {"intent": "highest_pod_pending_warehouse"}
        
        # DELIVERY STATUS QUERIES
        if any(phrase in question_lower for phrase in [
            "how many deliveries are completed", "completed deliveries count", "total completed deliveries"
        ]):
            return {"intent": "completed_deliveries_count"}
        
        if any(phrase in question_lower for phrase in [
            "how many deliveries are acknowledged", "acknowledged deliveries", "pod received count"
        ]):
            return {"intent": "acknowledged_deliveries"}
        
        if any(phrase in question_lower for phrase in [
            "delivered but not acknowledged", "awaiting dealer acknowledgement", "delivered pod pending"
        ]):
            return {"intent": "delivered_not_acknowledged"}
        
        # PENDING DELIVERIES
        pending_keywords = ["pending delivery", "pending deliveries", "how many pending", "pending orders"]
        if any(keyword in question_lower for keyword in pending_keywords):
            return {"intent": "pending_deliveries"}
        
        pod_keywords = ["pending pod", "pod pending", "delivery proof pending", "signature pending"]
        if any(keyword in question_lower for keyword in pod_keywords):
            return {"intent": "pending_pod"}
        
        pgi_keywords = ["pending pgi", "pgi pending", "goods issue pending", "not dispatched"]
        if any(keyword in question_lower for keyword in pgi_keywords):
            return {"intent": "pending_pgi"}
        
        # CITY SEARCH
        city_patterns = [
            r'in\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
            r'for\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
            r'at\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
        ]
        
        for pattern in city_patterns:
            city_match = re.search(pattern, question_lower)
            if city_match:
                city = city_match.group(1).strip().title()
                if city and len(city) > 2:
                    return {"intent": "city_search", "city": city}
        
        # MATERIAL / PRODUCT SEARCH
        material_patterns = [
            r'material[\s#:]*([a-zA-Z0-9]+)',
            r'product[\s#:]*([a-zA-Z0-9]+)',
        ]
        
        for pattern in material_patterns:
            material_match = re.search(pattern, question_lower)
            if material_match:
                return {"intent": "material_search", "material_no": material_match.group(1).upper()}
        
        # WAREHOUSE SEARCH
        warehouse_keywords = ["warehouse", "stock from", "from warehouse"]
        for keyword in warehouse_keywords:
            if keyword in question_lower:
                parts = question_lower.split(keyword)
                if len(parts) > 1:
                    warehouse = parts[1].strip().upper()
                    if warehouse and len(warehouse) >= 2:
                        return {"intent": "warehouse_search", "warehouse": warehouse}
        
        # EXECUTIVE / SUMMARY QUERIES
        executive_keywords = ["ceo", "executive", "performance", "report", "logistics summary", "dashboard overview"]
        if any(keyword in question_lower for keyword in executive_keywords):
            return {"intent": "executive_summary"}
        
        summary_keywords = ["summary", "dashboard", "overview", "statistics", "insights", "analytics"]
        if any(keyword in question_lower for keyword in summary_keywords):
            return {"intent": "dashboard_summary"}
        
        # TOP DEALERS / CITIES
        if "top" in question_lower:
            if "dealer" in question_lower or "customer" in question_lower:
                top_match = re.search(r'top\s+(\d+)', question_lower)
                return {"intent": "top_dealers", "limit": int(top_match.group(1)) if top_match else 10}
            if "city" in question_lower:
                top_match = re.search(r'top\s+(\d+)', question_lower)
                return {"intent": "top_cities", "limit": int(top_match.group(1)) if top_match else 10}
        
        # FALLBACK: Short text could be dealer name
        if 2 < len(question) < 30 and not re.search(r'\d', question):
            return {"intent": "dealer_lookup", "dealer_name": question.strip().title()}
        
        return {"intent": "general_query", "question": question}
    
    # ======================================================
    # DEALER FUNCTIONS (UPDATED WITH UNIQUE DN COUNTING)
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
    def get_dealer_summary(db: Session, dealer_name: str) -> Dict[str, Any]:
        """Get complete summary for a specific dealer with unique DN counting."""
        records = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
            .all()
        )
        
        if not records:
            return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
        
        # FIX: Unique DN counting
        unique_dns = LogisticsQueryService._get_unique_dns(records)
        total_dns = len(unique_dns)
        
        # Calculate delivered DNs (PGI Completed + POD Received)
        delivered_records = [r for r in records if r.pgi_status == "Completed" and r.pod_status == "Received"]
        delivered_dns = len(LogisticsQueryService._get_unique_dns(delivered_records))
        
        # Calculate pending DNs (pending_flag is True)
        pending_records = [r for r in records if r.pending_flag]
        pending_dns = len(LogisticsQueryService._get_unique_dns(pending_records))
        
        # Delivered but not acknowledged (PGI Completed, POD Pending)
        delivered_not_ack_records = [r for r in records if r.pgi_status == "Completed" and r.pod_status == "Pending"]
        delivered_not_ack_dns = len(LogisticsQueryService._get_unique_dns(delivered_not_ack_records))
        
        # Quantity calculations
        total_qty = sum(r.dn_qty or 0 for r in records)
        delivered_qty = sum(r.dn_qty or 0 for r in delivered_records)
        pending_qty = sum(r.dn_qty or 0 for r in pending_records)
        
        # Amount calculations
        total_amount = sum(r.dn_amount or 0 for r in records)
        pending_amount = sum(r.dn_amount or 0 for r in pending_records)
        
        actual_dealer_name = records[0].customer_name if records else dealer_name
        
        return {
            "success": True,
            "dealer_name": actual_dealer_name,
            "total_dns": total_dns,
            "delivered_dns": delivered_dns,
            "pending_dns": pending_dns,
            "delivered_not_acknowledged": delivered_not_ack_dns,
            "total_quantity": float(total_qty),
            "delivered_quantity": float(delivered_qty),
            "pending_quantity": float(pending_qty),
            "total_amount": float(total_amount),
            "pending_amount": float(pending_amount)
        }
    
    @staticmethod
    def get_dealer_dn_breakdown(db: Session, dealer_name: str) -> Dict[str, Any]:
        """Get DN breakdown for a specific dealer with products in each DN."""
        records = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
            .all()
        )
        
        if not records:
            return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
        
        # Group by DN
        dn_groups = {}
        for r in records:
            dn_no = r.dn_no
            if dn_no not in dn_groups:
                dn_groups[dn_no] = {
                    "dn_no": dn_no,
                    "status": "Delivered and Acknowledged" if (r.pgi_status == "Completed" and r.pod_status == "Received") else
                             "Delivered, Awaiting Acknowledgement" if (r.pgi_status == "Completed" and r.pod_status == "Pending") else
                             "Pending Dispatch" if r.pgi_status == "Pending" else r.delivery_status,
                    "total_quantity": 0,
                    "total_amount": 0,
                    "products": []
                }
            
            product = {
                "material_no": r.material_no,
                "product_name": r.customer_model or r.material_no,
                "quantity": float(r.dn_qty or 0),
                "amount": float(r.dn_amount or 0)
            }
            dn_groups[dn_no]["products"].append(product)
            dn_groups[dn_no]["total_quantity"] += product["quantity"]
            dn_groups[dn_no]["total_amount"] += product["amount"]
        
        actual_dealer_name = records[0].customer_name if records else dealer_name
        
        return {
            "success": True,
            "dealer_name": actual_dealer_name,
            "total_dns": len(dn_groups),
            "dns": list(dn_groups.values())
        }
    
    @staticmethod
    def get_dealer_product_summary(db: Session, dealer_name: str) -> Dict[str, Any]:
        """Get product-wise breakdown for a dealer with delivered/pending quantities."""
        records = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
            .all()
        )
        
        if not records:
            return {"success": False, "message": f"No products found for dealer '{dealer_name}'"}
        
        # Group by product (material_no)
        product_groups = {}
        for r in records:
            key = (r.material_no, r.customer_model)
            if key not in product_groups:
                product_groups[key] = {
                    "material_no": r.material_no,
                    "product_name": r.customer_model or r.material_no,
                    "total_quantity": 0,
                    "delivered_quantity": 0,
                    "pending_quantity": 0,
                    "total_amount": 0,
                    "pending_amount": 0,
                    "dn_count": set()
                }
            
            product_groups[key]["total_quantity"] += float(r.dn_qty or 0)
            product_groups[key]["total_amount"] += float(r.dn_amount or 0)
            product_groups[key]["dn_count"].add(r.dn_no)
            
            if r.pgi_status == "Completed":
                product_groups[key]["delivered_quantity"] += float(r.dn_qty or 0)
            if r.pending_flag:
                product_groups[key]["pending_quantity"] += float(r.dn_qty or 0)
                product_groups[key]["pending_amount"] += float(r.dn_amount or 0)
        
        actual_dealer_name = records[0].customer_name if records else dealer_name
        
        return {
            "success": True,
            "dealer_name": actual_dealer_name,
            "products": [
                {
                    "material_no": p["material_no"],
                    "product_name": p["product_name"],
                    "total_quantity": p["total_quantity"],
                    "delivered_quantity": p["delivered_quantity"],
                    "pending_quantity": p["pending_quantity"],
                    "total_amount": p["total_amount"],
                    "pending_amount": p["pending_amount"],
                    "dn_count": len(p["dn_count"])
                }
                for p in product_groups.values()
            ],
            "total_products": len(product_groups)
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
        
        # Business rules for status
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
            "products": products,
            "total_quantity": float(total_qty),
            "total_amount": float(total_amount)
        }
    
    # ======================================================
    # HIGHEST PENDING QUERIES (UPDATED WITH UNIQUE DN COUNTING)
    # ======================================================
    
    @staticmethod
    def get_highest_pending_dealer(db: Session) -> Dict[str, Any]:
        """Get dealer with highest number of pending DNs."""
        # Get all pending records
        pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).all()
        
        if not pending_records:
            return {"success": False, "message": "No pending deliveries found"}
        
        # Group by dealer and count unique DNs
        dealer_stats = {}
        for r in pending_records:
            dealer = r.customer_name
            if not dealer:
                continue
            if dealer not in dealer_stats:
                dealer_stats[dealer] = {"dns": set(), "amount": 0, "quantity": 0}
            dealer_stats[dealer]["dns"].add(r.dn_no)
            dealer_stats[dealer]["amount"] += float(r.dn_amount or 0)
            dealer_stats[dealer]["quantity"] += float(r.dn_qty or 0)
        
        if not dealer_stats:
            return {"success": False, "message": "No pending deliveries found"}
        
        # Find dealer with most pending DNs
        top_dealer = max(dealer_stats.items(), key=lambda x: len(x[1]["dns"]))
        
        return {
            "success": True,
            "dealer": top_dealer[0],
            "pending_count": len(top_dealer[1]["dns"]),
            "pending_amount": top_dealer[1]["amount"],
            "pending_quantity": top_dealer[1]["quantity"]
        }
    
    @staticmethod
    def get_highest_pending_warehouse(db: Session) -> Dict[str, Any]:
        """Get warehouse with highest number of pending DNs."""
        pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).all()
        
        if not pending_records:
            return {"success": False, "message": "No pending deliveries found"}
        
        warehouse_stats = {}
        for r in pending_records:
            warehouse = r.warehouse
            if not warehouse:
                continue
            if warehouse not in warehouse_stats:
                warehouse_stats[warehouse] = {"dns": set(), "amount": 0, "quantity": 0}
            warehouse_stats[warehouse]["dns"].add(r.dn_no)
            warehouse_stats[warehouse]["amount"] += float(r.dn_amount or 0)
            warehouse_stats[warehouse]["quantity"] += float(r.dn_qty or 0)
        
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
    def get_highest_pending_city(db: Session) -> Dict[str, Any]:
        """Get city with highest number of pending DNs."""
        pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).all()
        
        if not pending_records:
            return {"success": False, "message": "No pending deliveries found"}
        
        city_stats = {}
        for r in pending_records:
            city = r.ship_to_city
            if not city:
                continue
            if city not in city_stats:
                city_stats[city] = {"dns": set(), "amount": 0, "quantity": 0}
            city_stats[city]["dns"].add(r.dn_no)
            city_stats[city]["amount"] += float(r.dn_amount or 0)
            city_stats[city]["quantity"] += float(r.dn_qty or 0)
        
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
    
    # ======================================================
    # NEW: TOP PENDING RANKINGS
    # ======================================================
    
    @staticmethod
    def get_top_pending_warehouses(db: Session, limit: int = 10) -> Dict[str, Any]:
        """Get top warehouses by pending DNs."""
        pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).all()
        
        if not pending_records:
            return {"success": True, "warehouses": [], "count": 0}
        
        warehouse_stats = {}
        for r in pending_records:
            warehouse = r.warehouse
            if not warehouse:
                continue
            if warehouse not in warehouse_stats:
                warehouse_stats[warehouse] = {"dns": set(), "amount": 0, "quantity": 0}
            warehouse_stats[warehouse]["dns"].add(r.dn_no)
            warehouse_stats[warehouse]["amount"] += float(r.dn_amount or 0)
            warehouse_stats[warehouse]["quantity"] += float(r.dn_qty or 0)
        
        sorted_warehouses = sorted(warehouse_stats.items(), key=lambda x: len(x[1]["dns"]), reverse=True)[:limit]
        
        return {
            "success": True,
            "count": len(sorted_warehouses),
            "warehouses": [
                {
                    "rank": i + 1,
                    "warehouse": w[0],
                    "pending_dns": len(w[1]["dns"]),
                    "pending_quantity": w[1]["quantity"],
                    "pending_amount": w[1]["amount"]
                }
                for i, w in enumerate(sorted_warehouses)
            ]
        }
    
    @staticmethod
    def get_top_pending_cities(db: Session, limit: int = 10) -> Dict[str, Any]:
        """Get top cities by pending DNs."""
        pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).all()
        
        if not pending_records:
            return {"success": True, "cities": [], "count": 0}
        
        city_stats = {}
        for r in pending_records:
            city = r.ship_to_city
            if not city:
                continue
            if city not in city_stats:
                city_stats[city] = {"dns": set(), "amount": 0, "quantity": 0}
            city_stats[city]["dns"].add(r.dn_no)
            city_stats[city]["amount"] += float(r.dn_amount or 0)
            city_stats[city]["quantity"] += float(r.dn_qty or 0)
        
        sorted_cities = sorted(city_stats.items(), key=lambda x: len(x[1]["dns"]), reverse=True)[:limit]
        
        return {
            "success": True,
            "count": len(sorted_cities),
            "cities": [
                {
                    "rank": i + 1,
                    "city": c[0],
                    "pending_dns": len(c[1]["dns"]),
                    "pending_quantity": c[1]["quantity"],
                    "pending_amount": c[1]["amount"]
                }
                for i, c in enumerate(sorted_cities)
            ]
        }
    
    @staticmethod
    def get_top_pending_dealers(db: Session, limit: int = 10) -> Dict[str, Any]:
        """Get top dealers by pending DNs."""
        pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).all()
        
        if not pending_records:
            return {"success": True, "dealers": [], "count": 0}
        
        dealer_stats = {}
        for r in pending_records:
            dealer = r.customer_name
            if not dealer:
                continue
            if dealer not in dealer_stats:
                dealer_stats[dealer] = {"dns": set(), "amount": 0, "quantity": 0}
            dealer_stats[dealer]["dns"].add(r.dn_no)
            dealer_stats[dealer]["amount"] += float(r.dn_amount or 0)
            dealer_stats[dealer]["quantity"] += float(r.dn_qty or 0)
        
        sorted_dealers = sorted(dealer_stats.items(), key=lambda x: len(x[1]["dns"]), reverse=True)[:limit]
        
        return {
            "success": True,
            "count": len(sorted_dealers),
            "dealers": [
                {
                    "rank": i + 1,
                    "dealer": d[0],
                    "pending_dns": len(d[1]["dns"]),
                    "pending_quantity": d[1]["quantity"],
                    "pending_amount": d[1]["amount"]
                }
                for i, d in enumerate(sorted_dealers)
            ]
        }
    
    @staticmethod
    def get_pending_units_by_warehouse(db: Session) -> Dict[str, Any]:
        """Get pending units grouped by warehouse."""
        pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).all()
        
        if not pending_records:
            return {"success": True, "warehouses": [], "total_pending_units": 0}
        
        warehouse_stats = {}
        total_units = 0
        for r in pending_records:
            warehouse = r.warehouse
            if not warehouse:
                continue
            if warehouse not in warehouse_stats:
                warehouse_stats[warehouse] = {"dns": set(), "quantity": 0, "amount": 0}
            warehouse_stats[warehouse]["dns"].add(r.dn_no)
            warehouse_stats[warehouse]["quantity"] += float(r.dn_qty or 0)
            warehouse_stats[warehouse]["amount"] += float(r.dn_amount or 0)
            total_units += float(r.dn_qty or 0)
        
        return {
            "success": True,
            "total_pending_units": total_units,
            "warehouses": [
                {
                    "warehouse": w,
                    "pending_dns": len(stats["dns"]),
                    "pending_units": stats["quantity"],
                    "pending_amount": stats["amount"]
                }
                for w, stats in warehouse_stats.items()
            ]
        }
    
    @staticmethod
    def get_pending_units_by_city(db: Session) -> Dict[str, Any]:
        """Get pending units grouped by city."""
        pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).all()
        
        if not pending_records:
            return {"success": True, "cities": [], "total_pending_units": 0}
        
        city_stats = {}
        total_units = 0
        for r in pending_records:
            city = r.ship_to_city
            if not city:
                continue
            if city not in city_stats:
                city_stats[city] = {"dns": set(), "quantity": 0, "amount": 0}
            city_stats[city]["dns"].add(r.dn_no)
            city_stats[city]["quantity"] += float(r.dn_qty or 0)
            city_stats[city]["amount"] += float(r.dn_amount or 0)
            total_units += float(r.dn_qty or 0)
        
        return {
            "success": True,
            "total_pending_units": total_units,
            "cities": [
                {
                    "city": c,
                    "pending_dns": len(stats["dns"]),
                    "pending_units": stats["quantity"],
                    "pending_amount": stats["amount"]
                }
                for c, stats in city_stats.items()
            ]
        }
    
    # ======================================================
    # POD PENDING QUERIES (UPDATED)
    # ======================================================
    
    @staticmethod
    def get_highest_pod_pending_dealer(db: Session) -> Dict[str, Any]:
        """Get dealer with highest number of POD pending DNs."""
        pod_pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending",
            DeliveryReport.pgi_status == "Completed"
        ).all()
        
        if not pod_pending_records:
            return {"success": False, "message": "No POD pending deliveries found"}
        
        dealer_stats = {}
        for r in pod_pending_records:
            dealer = r.customer_name
            if not dealer:
                continue
            if dealer not in dealer_stats:
                dealer_stats[dealer] = {"dns": set(), "amount": 0, "quantity": 0}
            dealer_stats[dealer]["dns"].add(r.dn_no)
            dealer_stats[dealer]["amount"] += float(r.dn_amount or 0)
            dealer_stats[dealer]["quantity"] += float(r.dn_qty or 0)
        
        top_dealer = max(dealer_stats.items(), key=lambda x: len(x[1]["dns"]))
        
        return {
            "success": True,
            "dealer": top_dealer[0],
            "pod_pending_count": len(top_dealer[1]["dns"]),
            "pod_pending_amount": top_dealer[1]["amount"],
            "pod_pending_quantity": top_dealer[1]["quantity"]
        }
    
    @staticmethod
    def get_highest_pod_pending_warehouse(db: Session) -> Dict[str, Any]:
        """Get warehouse with highest number of POD pending DNs."""
        pod_pending_records = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending",
            DeliveryReport.pgi_status == "Completed"
        ).all()
        
        if not pod_pending_records:
            return {"success": False, "message": "No POD pending deliveries found"}
        
        warehouse_stats = {}
        for r in pod_pending_records:
            warehouse = r.warehouse
            if not warehouse:
                continue
            if warehouse not in warehouse_stats:
                warehouse_stats[warehouse] = {"dns": set(), "amount": 0, "quantity": 0}
            warehouse_stats[warehouse]["dns"].add(r.dn_no)
            warehouse_stats[warehouse]["amount"] += float(r.dn_amount or 0)
            warehouse_stats[warehouse]["quantity"] += float(r.dn_qty or 0)
        
        top_warehouse = max(warehouse_stats.items(), key=lambda x: len(x[1]["dns"]))
        
        return {
            "success": True,
            "warehouse": top_warehouse[0],
            "pod_pending_count": len(top_warehouse[1]["dns"]),
            "pod_pending_amount": top_warehouse[1]["amount"],
            "pod_pending_quantity": top_warehouse[1]["quantity"]
        }
    
    # ======================================================
    # DELIVERY COMPLETION QUERIES (UPDATED WITH UNIQUE DN COUNTING)
    # ======================================================
    
    @staticmethod
    def get_completed_deliveries_count(db: Session) -> Dict[str, Any]:
        """Get count of completed deliveries (PGI Completed + POD Received)."""
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
    def get_acknowledged_deliveries(db: Session) -> Dict[str, Any]:
        """Get deliveries that have been acknowledged (POD Received)."""
        rows = db.query(DeliveryReport).filter(DeliveryReport.pod_status == "Received").all()
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        unique_dns = LogisticsQueryService._get_unique_dns(rows)
        total_amount = sum(r.get("dn_amount", 0) for r in records)
        total_quantity = sum(r.get("dn_qty", 0) for r in records)
        
        return {
            "success": True,
            "acknowledged_count": len(unique_dns),
            "acknowledged_amount": float(total_amount),
            "acknowledged_quantity": float(total_quantity),
            "records": records
        }
    
    @staticmethod
    def get_delivered_not_acknowledged(db: Session) -> Dict[str, Any]:
        """Get deliveries delivered but not acknowledged."""
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
            "delivered_not_acknowledged_quantity": float(total_quantity),
            "records": records
        }
    
    # ======================================================
    # AI CONTEXT BUILDER (UPDATED)
    # ======================================================
    
    @staticmethod
    def generate_ai_context(question: str, db: Session) -> Dict[str, Any]:
        """Generate rich AI context from natural language question."""
        intent_result = LogisticsQueryService.detect_intent(question)
        intent = intent_result["intent"]
        
        if intent == "dn_lookup":
            result = LogisticsQueryService.get_dn_product_breakdown(db, intent_result["dn_no"])
            result["summary"] = LogisticsQueryService.generate_dn_summary(result)
        
        elif intent == "dealer_lookup":
            result = LogisticsQueryService.get_dealer_summary(db, intent_result.get("dealer_name", ""))
            if not result.get("success"):
                fuzzy_result = LogisticsQueryService.search_dealer_fuzzy(db, intent_result.get("dealer_name", ""))
                if fuzzy_result.get("success"):
                    result = fuzzy_result
                    result["summary"] = LogisticsQueryService.generate_fuzzy_dealer_summary(result)
                else:
                    result["summary"] = f"No dealer found matching '{intent_result.get('dealer_name', '')}'"
            else:
                result["summary"] = LogisticsQueryService.generate_dealer_summary_text(result)
        
        elif intent == "dealer_dn_breakdown":
            result = LogisticsQueryService.get_dealer_dn_breakdown(db, intent_result.get("dealer_name", ""))
            if result.get("success"):
                result["summary"] = LogisticsQueryService.generate_dealer_dn_breakdown_text(result)
            else:
                result["summary"] = f"No DNs found for dealer '{intent_result.get('dealer_name', '')}'"
        
        elif intent == "dealer_products":
            result = LogisticsQueryService.get_dealer_product_summary(db, intent_result.get("dealer_name", ""))
            if result.get("success"):
                result["summary"] = LogisticsQueryService.generate_dealer_products_text(result)
            else:
                result["summary"] = f"No products found for dealer '{intent_result.get('dealer_name', '')}'"
        
        elif intent == "pending_deliveries":
            result = LogisticsQueryService.get_pending_deliveries(db)
            result["summary"] = LogisticsQueryService.generate_pending_summary(result)
        
        elif intent == "pending_pod":
            result = LogisticsQueryService.get_pending_pod(db)
            result["summary"] = f"There are {result['pending_pod']} deliveries awaiting dealer acknowledgement."
        
        elif intent == "pending_pgi":
            result = LogisticsQueryService.get_pending_pgi(db)
            result["summary"] = f"There are {result['pending_pgi']} deliveries pending dispatch from warehouse."
        
        elif intent == "highest_pending_dealer":
            result = LogisticsQueryService.get_highest_pending_dealer(db)
            if result.get("success"):
                result["summary"] = (f"{result['dealer']} currently has the highest pending deliveries "
                                    f"with {result['pending_count']} pending DNs, {result['pending_quantity']:.0f} units, "
                                    f"totaling Rs {result['pending_amount']:,.2f}.")
            else:
                result["summary"] = "No pending deliveries found in the system."
        
        elif intent == "highest_pending_warehouse":
            result = LogisticsQueryService.get_highest_pending_warehouse(db)
            if result.get("success"):
                result["summary"] = (f"Warehouse {result['warehouse']} currently has the highest pending deliveries "
                                    f"with {result['pending_count']} pending DNs, {result['pending_quantity']:.0f} units, "
                                    f"totaling Rs {result['pending_amount']:,.2f}.")
            else:
                result["summary"] = "No pending deliveries found in the system."
        
        elif intent == "top_pending_warehouses":
            limit = intent_result.get("limit", 10)
            result = LogisticsQueryService.get_top_pending_warehouses(db, limit)
            if result.get("success") and result.get("warehouses"):
                warehouse_list = "\n".join([f"{w['rank']}. {w['warehouse']}: {w['pending_dns']} DNs, {w['pending_quantity']:.0f} units" 
                                           for w in result["warehouses"]])
                result["summary"] = f"Top {len(result['warehouses'])} Warehouses by Pending:\n{warehouse_list}"
            else:
                result["summary"] = "No pending deliveries found."
        
        elif intent == "top_pending_cities":
            limit = intent_result.get("limit", 10)
            result = LogisticsQueryService.get_top_pending_cities(db, limit)
            if result.get("success") and result.get("cities"):
                city_list = "\n".join([f"{c['rank']}. {c['city']}: {c['pending_dns']} DNs, {c['pending_quantity']:.0f} units" 
                                      for c in result["cities"]])
                result["summary"] = f"Top {len(result['cities'])} Cities by Pending:\n{city_list}"
            else:
                result["summary"] = "No pending deliveries found."
        
        elif intent == "top_pending_dealers":
            limit = intent_result.get("limit", 10)
            result = LogisticsQueryService.get_top_pending_dealers(db, limit)
            if result.get("success") and result.get("dealers"):
                dealer_list = "\n".join([f"{d['rank']}. {d['dealer']}: {d['pending_dns']} DNs, {d['pending_quantity']:.0f} units" 
                                        for d in result["dealers"]])
                result["summary"] = f"Top {len(result['dealers'])} Dealers by Pending:\n{dealer_list}"
            else:
                result["summary"] = "No pending deliveries found."
        
        elif intent == "pending_units_by_warehouse":
            result = LogisticsQueryService.get_pending_units_by_warehouse(db)
            if result.get("success") and result.get("warehouses"):
                warehouse_list = "\n".join([f"• {w['warehouse']}: {w['pending_dns']} DNs, {w['pending_units']:.0f} units, Rs {w['pending_amount']:,.2f}" 
                                           for w in result["warehouses"]])
                result["summary"] = f"Pending Units by Warehouse (Total: {result['total_pending_units']:.0f} units):\n{warehouse_list}"
            else:
                result["summary"] = "No pending deliveries found."
        
        elif intent == "pending_units_by_city":
            result = LogisticsQueryService.get_pending_units_by_city(db)
            if result.get("success") and result.get("cities"):
                city_list = "\n".join([f"• {c['city']}: {c['pending_dns']} DNs, {c['pending_units']:.0f} units, Rs {c['pending_amount']:,.2f}" 
                                      for c in result["cities"]])
                result["summary"] = f"Pending Units by City (Total: {result['total_pending_units']:.0f} units):\n{city_list}"
            else:
                result["summary"] = "No pending deliveries found."
        
        elif intent == "highest_pending_city":
            result = LogisticsQueryService.get_highest_pending_city(db)
            if result.get("success"):
                result["summary"] = (f"{result['city']} currently has the highest pending deliveries "
                                    f"with {result['pending_count']} pending DNs, {result['pending_quantity']:.0f} units, "
                                    f"totaling Rs {result['pending_amount']:,.2f}.")
            else:
                result["summary"] = "No pending deliveries found in the system."
        
        elif intent == "highest_pod_pending_dealer":
            result = LogisticsQueryService.get_highest_pod_pending_dealer(db)
            if result.get("success"):
                result["summary"] = (f"{result['dealer']} has the most deliveries awaiting acknowledgement "
                                    f"with {result['pod_pending_count']} DNs, {result['pod_pending_quantity']:.0f} units, "
                                    f"totaling Rs {result['pod_pending_amount']:,.2f}.")
            else:
                result["summary"] = "No POD pending deliveries found."
        
        elif intent == "highest_pod_pending_warehouse":
            result = LogisticsQueryService.get_highest_pod_pending_warehouse(db)
            if result.get("success"):
                result["summary"] = (f"Warehouse {result['warehouse']} has the most deliveries awaiting acknowledgement "
                                    f"with {result['pod_pending_count']} DNs, {result['pod_pending_quantity']:.0f} units, "
                                    f"totaling Rs {result['pod_pending_amount']:,.2f}.")
            else:
                result["summary"] = "No POD pending deliveries found."
        
        elif intent == "completed_deliveries_count":
            result = LogisticsQueryService.get_completed_deliveries_count(db)
            result["summary"] = (f"There are {result['completed_count']} completed deliveries "
                                f"totaling {result['completed_quantity']:.0f} units worth Rs {result['completed_amount']:,.2f}.")
        
        elif intent == "acknowledged_deliveries":
            result = LogisticsQueryService.get_acknowledged_deliveries(db)
            result["summary"] = (f"There are {result['acknowledged_count']} acknowledged deliveries "
                                f"totaling {result['acknowledged_quantity']:.0f} units worth Rs {result['acknowledged_amount']:,.2f}.")
        
        elif intent == "delivered_not_acknowledged":
            result = LogisticsQueryService.get_delivered_not_acknowledged(db)
            result["summary"] = (f"There are {result['delivered_not_acknowledged_count']} deliveries that have been dispatched "
                                f"but are awaiting dealer acknowledgement, totaling {result['delivered_not_acknowledged_quantity']:.0f} units "
                                f"worth Rs {result['delivered_not_acknowledged_amount']:,.2f}.")
        
        elif intent == "city_search":
            result = LogisticsQueryService.get_city_deliveries(db, intent_result["city"])
            result["summary"] = LogisticsQueryService.generate_city_summary(result)
        
        elif intent == "material_search":
            result = LogisticsQueryService.search_material(db, intent_result["material_no"])
            result["summary"] = LogisticsQueryService.generate_material_summary(result)
        
        elif intent == "warehouse_search":
            result = LogisticsQueryService.get_warehouse_deliveries(db, intent_result["warehouse"])
            result["summary"] = LogisticsQueryService.generate_warehouse_summary(result)
        
        elif intent == "dashboard_summary":
            result = LogisticsQueryService.get_delivery_insights(db)
            result["summary"] = LogisticsQueryService.generate_insights_summary(result)
        
        elif intent == "executive_summary":
            result = LogisticsQueryService.get_executive_summary(db)
            result["summary"] = result.get("executive_summary", "Executive summary generated successfully.")
        
        elif intent == "top_dealers":
            limit = intent_result.get("limit", 10)
            result = LogisticsQueryService.get_top_dealers(db, limit)
            result["summary"] = f"Top dealers: {', '.join([d['dealer_name'] for d in result['records'][:5]])}"
        
        elif intent == "top_cities":
            limit = intent_result.get("limit", 10)
            result = LogisticsQueryService.get_top_cities(db, limit)
            result["summary"] = f"Top cities by delivery volume: {', '.join([c['city'] for c in result[:5]])}"
        
        else:
            result = LogisticsQueryService.get_delivery_insights(db)
            result["summary"] = "Here's the current logistics dashboard summary."
        
        result["question"] = question
        result["intent"] = intent
        
        return result
    
    # ======================================================
    # AI SUMMARY GENERATORS (UPDATED)
    # ======================================================
    
    @staticmethod
    def generate_dn_summary(dn_result: Dict[str, Any]) -> str:
        """Generate natural language summary for DN lookup."""
        if not dn_result.get("success"):
            return f"DN {dn_result.get('dn_no', 'unknown')} was not found in the system."
        
        dn_no = dn_result.get("dn_no", "Unknown")
        dealer = dn_result.get("dealer", "Unknown Customer")
        city = dn_result.get("city", "Unknown City")
        warehouse = dn_result.get("warehouse", "Unknown Warehouse")
        status = dn_result.get("status", "Unknown")
        total_amount = dn_result.get("total_amount", 0)
        total_quantity = dn_result.get("total_quantity", 0)
        products = dn_result.get("products", [])
        
        summary = f"DN {dn_no} belongs to {dealer} in {city}. "
        
        if status == "Delivered and Acknowledged":
            summary += "The shipment has been delivered and acknowledged by the dealer. "
        elif status == "Delivered, Awaiting Acknowledgement":
            summary += "The shipment has been delivered and is awaiting dealer acknowledgement. "
        elif status == "Pending Dispatch":
            summary += "The shipment is pending dispatch from the warehouse. "
        else:
            summary += f"The shipment status is {status}. "
        
        summary += f"Warehouse: {warehouse}. Total Quantity: {total_quantity:.0f} units. Amount: Rs {total_amount:,.2f}."
        
        if products:
            product_list = ", ".join([f"{p['product_name']} ({p['quantity']:.0f} units)" for p in products[:3]])
            summary += f" Products: {product_list}."
            if len(products) > 3:
                summary += f" And {len(products) - 3} more items."
        
        return summary
    
    @staticmethod
    def generate_dealer_summary_text(result: Dict[str, Any]) -> str:
        """Generate natural language dealer summary."""
        return (
            f"📊 DEALER SUMMARY: {result['dealer_name']}\n\n"
            f"📦 Deliveries:\n"
            f"• Total DNs: {result['total_dns']}\n"
            f"• Delivered: {result['delivered_dns']}\n"
            f"• Pending: {result['pending_dns']}\n"
            f"• Awaiting Acknowledgement: {result['delivered_not_acknowledged']}\n\n"
            f"📦 Quantity:\n"
            f"• Total Units: {result['total_quantity']:.0f}\n"
            f"• Delivered: {result['delivered_quantity']:.0f}\n"
            f"• Pending: {result['pending_quantity']:.0f}\n\n"
            f"💰 Amount:\n"
            f"• Total Value: Rs {result['total_amount']:,.2f}\n"
            f"• Pending Value: Rs {result['pending_amount']:,.2f}"
        )
    
    @staticmethod
    def generate_dealer_dn_breakdown_text(result: Dict[str, Any]) -> str:
        """Generate summary for dealer DN breakdown."""
        dealer_name = result.get("dealer_name", "Unknown")
        dns = result.get("dns", [])
        
        if not dns:
            return f"No DNs found for {dealer_name}"
        
        text = f"📋 DN BREAKDOWN FOR {dealer_name}:\n\n"
        for dn in dns[:10]:
            text += f"🔹 DN {dn['dn_no']}: {dn['status']}\n"
            text += f"   Total Units: {dn['total_quantity']:.0f}\n"
            for product in dn['products'][:3]:
                text += f"   • {product['product_name']}: {product['quantity']:.0f} units\n"
            if len(dn['products']) > 3:
                text += f"   • And {len(dn['products']) - 3} more products\n"
            text += "\n"
        
        if len(dns) > 10:
            text += f"And {len(dns) - 10} more DNs.\n"
        
        return text
    
    @staticmethod
    def generate_dealer_products_text(result: Dict[str, Any]) -> str:
        """Generate natural language dealer products summary."""
        dealer_name = result.get("dealer_name", "Unknown")
        products = result.get("products", [])
        
        if not products:
            return f"No products found for {dealer_name}"
        
        text = f"📦 PRODUCTS FOR {dealer_name}:\n\n"
        for p in products[:10]:
            text += (f"• {p['product_name']}: {p['total_quantity']:.0f} total units, "
                    f"{p['delivered_quantity']:.0f} delivered, {p['pending_quantity']:.0f} pending\n")
        
        if len(products) > 10:
            text += f"\nAnd {len(products) - 10} more products."
        
        return text
    
    @staticmethod
    def generate_fuzzy_dealer_summary(result: Dict[str, Any]) -> str:
        """Generate summary for fuzzy dealer search."""
        matches = result.get("matches", [])
        if not matches:
            return f"No dealers found matching '{result.get('search_term', '')}'"
        
        text = f"Multiple dealers found matching '{result['search_term']}':\n\n"
        for i, m in enumerate(matches[:5], 1):
            text += f"{i}. {m['dealer_name']} - {m['total_dns']} DNs, Rs {m['total_amount']:,.2f}\n"
        
        text += "\nPlease reply with the number of your dealer for details."
        return text
    
    @staticmethod
    def generate_pending_summary(result: Dict[str, Any]) -> str:
        """Generate summary for pending deliveries."""
        count = result.get("count", 0)
        if count == 0:
            return "There are no pending deliveries. All deliveries are complete!"
        
        total_amount = result.get("total_amount", 0)
        total_quantity = result.get("total_quantity", 0)
        
        return (f"There are {count} pending DNs totaling {total_quantity:.0f} units worth Rs {total_amount:,.2f}. "
                f"These deliveries have not been completed yet.")
    
    @staticmethod
    def generate_city_summary(result: Dict[str, Any]) -> str:
        """Generate summary for city search."""
        city = result.get("city", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for {city}."
        
        records = result.get("records", [])
        unique_dns = set(r.get("dn_no") for r in records if r.get("dn_no"))
        
        return f"Found {len(unique_dns)} DNs for {city}."
    
    @staticmethod
    def generate_material_summary(result: Dict[str, Any]) -> str:
        """Generate summary for material search."""
        material = result.get("material_no", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for material {material}."
        
        total_qty = result.get("total_quantity", 0)
        
        return f"Material {material} appears in {count} deliveries with total quantity {total_qty:.0f} units."
    
    @staticmethod
    def generate_warehouse_summary(result: Dict[str, Any]) -> str:
        """Generate summary for warehouse search."""
        warehouse = result.get("warehouse", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for warehouse {warehouse}."
        
        return f"Warehouse {warehouse} has {count} deliveries."
    
    @staticmethod
    def generate_insights_summary(result: Dict[str, Any]) -> str:
        """Generate summary for delivery insights."""
        return (
            f"Logistics Summary: {result['total_records']} total DNs. "
            f"{result['pending_deliveries']} pending (Rs {result['pending_amount']:,.2f}). "
            f"Top city: {result['top_city']}. Top warehouse: {result['top_warehouse']}."
        )
    
    # ======================================================
    # GPT PROMPT BUILDER
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

Never show PGI/POD. Use: Delivered, Pending Dispatch, Acknowledged, Awaiting Acknowledgement

RESPONSE:"""
        
        return prompt
    
    # ======================================================
    # UNIFIED AI QUERY HANDLER
    # ======================================================
    
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
    # DELIVERY ANALYTICS (UPDATED WITH UNIQUE DN COUNTING)
    # ======================================================
    
    @staticmethod
    def get_delivery_insights(db: Session) -> Dict[str, Any]:
        """Get comprehensive delivery analytics with unique DN counting."""
        all_records = db.query(DeliveryReport).all()
        unique_dns = LogisticsQueryService._get_unique_dns(all_records)
        
        pending_records = db.query(DeliveryReport).filter(DeliveryReport.pending_flag.is_(True)).all()
        pending_unique_dns = LogisticsQueryService._get_unique_dns(pending_records)
        
        completed_records = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Received"
        ).all()
        completed_unique_dns = LogisticsQueryService._get_unique_dns(completed_records)
        
        delivered_not_ack_records = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending"
        ).all()
        delivered_not_ack_unique_dns = LogisticsQueryService._get_unique_dns(delivered_not_ack_records)
        
        pending_amount = sum(r.dn_amount or 0 for r in pending_records)
        
        # Top warehouse
        warehouse_stats = {}
        for r in all_records:
            if r.warehouse:
                if r.warehouse not in warehouse_stats:
                    warehouse_stats[r.warehouse] = set()
                warehouse_stats[r.warehouse].add(r.dn_no)
        
        top_warehouse = max(warehouse_stats.items(), key=lambda x: len(x[1]))[0] if warehouse_stats else "N/A"
        
        # Top city
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
    
    # ======================================================
    # EXECUTIVE SUMMARY (UPDATED)
    # ======================================================
    
    @staticmethod
    def get_executive_summary(db: Session) -> Dict[str, Any]:
        """Get executive-level dashboard summary."""
        insights = LogisticsQueryService.get_delivery_insights(db)
        top_dealers = LogisticsQueryService.get_top_dealers(db, 5)
        top_cities = LogisticsQueryService.get_top_cities(db, 5)
        top_pending_warehouses = LogisticsQueryService.get_top_pending_warehouses(db, 5)
        top_pending_dealers = LogisticsQueryService.get_top_pending_dealers(db, 5)
        highest_pending_dealer = LogisticsQueryService.get_highest_pending_dealer(db)
        
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

🏆 TOP PERFORMERS:
• Top City: {insights['top_city']}
• Top Warehouse: {insights['top_warehouse']}
• Top Dealer: {top_dealers['records'][0]['dealer_name'] if top_dealers['records'] else 'N/A'}

⚠️ HIGHEST PENDING:
• Dealer: {highest_pending_dealer.get('dealer', 'N/A')} ({highest_pending_dealer.get('pending_count', 0)} DNs)
• Warehouse: {top_pending_warehouses.get('warehouses', [{}])[0].get('warehouse', 'N/A') if top_pending_warehouses.get('warehouses') else 'N/A'}

🎯 RECOMMENDATIONS:
• Focus on clearing pending dispatches
• Follow up on unacknowledged deliveries
• Monitor top pending dealers"""
        
        return {
            "success": True,
            "executive_summary": executive_summary,
            **insights,
            "top_dealers": top_dealers["records"],
            "top_cities": top_cities,
            "top_pending_warehouses": top_pending_warehouses.get("warehouses", []),
            "top_pending_dealers": top_pending_dealers.get("dealers", []),
            "highest_pending_dealer": highest_pending_dealer
        }
    
    # ======================================================
    # ORIGINAL METHODS (UPDATED WITH UNIQUE DN COUNTING)
    # ======================================================
    
    @staticmethod
    def get_pending_deliveries(db: Session, limit: int = 100):
        rows = db.query(DeliveryReport).filter(DeliveryReport.pending_flag.is_(True)).all()
        
        # Group by DN for unique counting
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
    def get_pending_pod(db: Session):
        rows = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending",
            DeliveryReport.pgi_status == "Completed"
        ).all()
        unique_dns = LogisticsQueryService._get_unique_dns(rows)
        
        return {"success": True, "pending_pod": len(unique_dns)}
    
    @staticmethod
    def get_pending_pgi(db: Session):
        rows = db.query(DeliveryReport).filter(DeliveryReport.pgi_status == "Pending").all()
        unique_dns = LogisticsQueryService._get_unique_dns(rows)
        
        return {"success": True, "pending_pgi": len(unique_dns)}
    
    @staticmethod
    def get_city_deliveries(db: Session, city: str, limit: int = 100):
        rows = db.query(DeliveryReport).filter(DeliveryReport.ship_to_city.ilike(f"%{city}%")).all()
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        
        return {"success": True, "city": city, "count": len(records), "records": records}
    
    @staticmethod
    def get_warehouse_deliveries(db: Session, warehouse: str, limit: int = 100):
        rows = db.query(DeliveryReport).filter(DeliveryReport.warehouse.ilike(f"%{warehouse}%")).all()
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        
        return {"success": True, "warehouse": warehouse, "count": len(records), "records": records}
    
    @staticmethod
    def search_material(db: Session, material_no: str, limit: int = 50) -> Dict[str, Any]:
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
    def get_top_dealers(db: Session, limit: int = 10) -> Dict[str, Any]:
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
    def get_top_cities(db: Session, limit: int = 10):
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
