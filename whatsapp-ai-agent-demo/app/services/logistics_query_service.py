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
    # INTENT DETECTION (FULLY ENHANCED)
    # ======================================================
    
    @staticmethod
    def detect_intent(question: str) -> Dict[str, Any]:
        """Detect user intent from natural language question."""
        original_question = question
        question_lower = LogisticsQueryService._normalize_question(question)
        
        # ==================================================
        # DN NUMBER DETECTION
        # ==================================================
        dn_match = re.search(r'\b(\d{8,15})\b', question)
        if dn_match:
            return {"intent": "dn_lookup", "dn_no": dn_match.group(1)}
        
        dn_keywords = ["dn", "delivery note", "delivery number", "status of dn", "check dn", "show dn"]
        for keyword in dn_keywords:
            if keyword in question_lower:
                dn_match_with_keyword = re.search(r'\b(\d{8,15})\b', question)
                if dn_match_with_keyword:
                    return {"intent": "dn_lookup", "dn_no": dn_match_with_keyword.group(1)}
        
        # ==================================================
        # AGING & DELAY INTELLIGENCE (NEW)
        # ==================================================
        
        if any(phrase in question_lower for phrase in [
            "aging report", "delivery aging", "pending aging", "old pending", "oldest pending"
        ]):
            return {"intent": "pending_delivery_aging"}
        
        if any(phrase in question_lower for phrase in [
            "dealer aging", "aging for dealer", "show aging for", "dealer aging summary"
        ]):
            dealer_match = re.search(r'(?:for|of|from)\s+([a-zA-Z\s]+)', question_lower)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip().title()
                if dealer_name and len(dealer_name) > 2:
                    return {"intent": "dealer_aging", "dealer_name": dealer_name}
        
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
        
        # Aging by days threshold
        days_match = re.search(r'older than (\d+) days?', question_lower)
        if days_match:
            days = int(days_match.group(1))
            return {"intent": "pending_older_than", "days": days}
        
        # ==================================================
        # DEALER LOOKUP
        # ==================================================
        
        dealer_phrases = [
            "status of", "show me", "tell me about", "deliveries of", 
            "delivery for", "information about", "details of"
        ]
        
        for phrase in dealer_phrases:
            if phrase in question_lower:
                dealer_match = re.search(r'(?:' + phrase + r')\s+([a-zA-Z\s]+)', question_lower)
                if dealer_match:
                    dealer_name = dealer_match.group(1).strip().title()
                    if dealer_name and len(dealer_name) > 2:
                        return {"intent": "dealer_lookup", "dealer_name": dealer_name}
        
        dealer_keywords = ["dealer", "customer", "show me deliveries for", "deliveries of", "tell me about"]
        for keyword in dealer_keywords:
            if keyword in question_lower:
                parts = question_lower.split(keyword)
                if len(parts) > 1:
                    dealer_name = parts[1].strip().title()
                    if dealer_name and len(dealer_name) > 2:
                        return {"intent": "dealer_lookup", "dealer_name": dealer_name}
        
        # Dealer DN breakdown
        if any(phrase in question_lower for phrase in ["dn for", "delivery notes for", "show dns for", "list dns for"]):
            dealer_match = re.search(r'(?:for|of|from)\s+([a-zA-Z\s]+)', question_lower)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip().title()
                if dealer_name and len(dealer_name) > 2:
                    return {"intent": "dealer_dn_breakdown", "dealer_name": dealer_name}
        
        # Dealer products
        if any(phrase in question_lower for phrase in [
            "products for", "what products", "items for", "product breakdown",
            "models for", "what models", "quantities by model"
        ]):
            dealer_match = re.search(r'(?:for|of|from)\s+([a-zA-Z\s]+)', question_lower)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip().title()
                if dealer_name and len(dealer_name) > 2:
                    return {"intent": "dealer_products", "dealer_name": dealer_name}
        
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
        
        # ==================================================
        # FALLBACK: Short text could be dealer name
        # ==================================================
        
        if 2 < len(original_question) < 30 and not re.search(r'\d', original_question):
            return {"intent": "dealer_lookup", "dealer_name": original_question.strip().title()}
        
        return {"intent": "general_query", "question": original_question}
    
    # ======================================================
    # AGING & DELIVERY INTELLIGENCE FUNCTIONS (NEW)
    # ======================================================
    
    @staticmethod
    def get_pending_delivery_aging(db: Session) -> Dict[str, Any]:
        """Get aging breakdown of pending deliveries by days."""
        all_records = db.query(DeliveryReport).all()
        
        # Use business rule: pending = PGI not completed
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "message": "No pending deliveries found"}
        
        # Age categories
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
        
        return {
            "success": True,
            "total_pending_dns": total_pending,
            "aging_breakdown": aging_groups
        }
    
    @staticmethod
    def get_dealer_aging(db: Session, dealer_name: str) -> Dict[str, Any]:
        """Get aging summary for a specific dealer."""
        records = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
            .all()
        )
        
        if not records:
            return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
        
        # Use business rule: pending = PGI not completed
        pending_records = [r for r in records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {
                "success": True,
                "dealer_name": records[0].customer_name,
                "message": "No pending deliveries for this dealer"
            }
        
        aging_groups = {
            "0-3 Days": {"dns": set(), "quantity": 0},
            "4-7 Days": {"dns": set(), "quantity": 0},
            "8-15 Days": {"dns": set(), "quantity": 0},
            "16-30 Days": {"dns": set(), "quantity": 0},
            "30+ Days": {"dns": set(), "quantity": 0}
        }
        
        oldest_dn = None
        max_age = 0
        
        for r in pending_records:
            age_days = LogisticsQueryService._calculate_dn_age(r)
            
            if age_days > max_age:
                max_age = age_days
                oldest_dn = r.dn_no
            
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
        
        actual_dealer_name = records[0].customer_name if records else dealer_name
        
        return {
            "success": True,
            "dealer_name": actual_dealer_name,
            "aging_breakdown": aging_groups,
            "oldest_dn": oldest_dn,
            "oldest_age_days": max_age
        }
    
    @staticmethod
    def get_warehouse_aging(db: Session, limit: int = 10) -> Dict[str, Any]:
        """Get warehouses ranked by oldest pending delivery age."""
        all_records = db.query(DeliveryReport).all()
        
        # Use business rule: pending = PGI not completed
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "warehouses": [], "count": 0}
        
        warehouse_stats = {}
        for r in pending_records:
            if not r.warehouse:
                continue
            if r.warehouse not in warehouse_stats:
                warehouse_stats[r.warehouse] = {
                    "dns": set(),
                    "max_age": 0,
                    "oldest_dn": None,
                    "quantity": 0
                }
            
            age = LogisticsQueryService._calculate_dn_age(r)
            warehouse_stats[r.warehouse]["dns"].add(r.dn_no)
            warehouse_stats[r.warehouse]["quantity"] += float(r.dn_qty or 0)
            
            if age > warehouse_stats[r.warehouse]["max_age"]:
                warehouse_stats[r.warehouse]["max_age"] = age
                warehouse_stats[r.warehouse]["oldest_dn"] = r.dn_no
        
        sorted_warehouses = sorted(
            warehouse_stats.items(),
            key=lambda x: x[1]["max_age"],
            reverse=True
        )[:limit]
        
        return {
            "success": True,
            "count": len(sorted_warehouses),
            "warehouses": [
                {
                    "rank": i + 1,
                    "warehouse": w[0],
                    "pending_dns": len(w[1]["dns"]),
                    "pending_quantity": w[1]["quantity"],
                    "oldest_age_days": w[1]["max_age"],
                    "oldest_dn": w[1]["oldest_dn"]
                }
                for i, w in enumerate(sorted_warehouses)
            ]
        }
    
    @staticmethod
    def get_city_aging(db: Session, limit: int = 10) -> Dict[str, Any]:
        """Get cities ranked by oldest pending delivery age."""
        all_records = db.query(DeliveryReport).all()
        
        # Use business rule: pending = PGI not completed
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": True, "cities": [], "count": 0}
        
        city_stats = {}
        for r in pending_records:
            if not r.ship_to_city:
                continue
            if r.ship_to_city not in city_stats:
                city_stats[r.ship_to_city] = {
                    "dns": set(),
                    "max_age": 0,
                    "oldest_dn": None,
                    "quantity": 0
                }
            
            age = LogisticsQueryService._calculate_dn_age(r)
            city_stats[r.ship_to_city]["dns"].add(r.dn_no)
            city_stats[r.ship_to_city]["quantity"] += float(r.dn_qty or 0)
            
            if age > city_stats[r.ship_to_city]["max_age"]:
                city_stats[r.ship_to_city]["max_age"] = age
                city_stats[r.ship_to_city]["oldest_dn"] = r.dn_no
        
        sorted_cities = sorted(
            city_stats.items(),
            key=lambda x: x[1]["max_age"],
            reverse=True
        )[:limit]
        
        return {
            "success": True,
            "count": len(sorted_cities),
            "cities": [
                {
                    "rank": i + 1,
                    "city": c[0],
                    "pending_dns": len(c[1]["dns"]),
                    "pending_quantity": c[1]["quantity"],
                    "oldest_age_days": c[1]["max_age"],
                    "oldest_dn": c[1]["oldest_dn"]
                }
                for i, c in enumerate(sorted_cities)
            ]
        }
    
    @staticmethod
    def get_critical_pending_dns(db: Session, threshold_days: int = 15, limit: int = 20) -> Dict[str, Any]:
        """Get critical pending DNs (older than threshold days)."""
        all_records = db.query(DeliveryReport).all()
        
        # Use business rule: pending = PGI not completed
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        critical_dns = []
        for r in pending_records:
            age_days = LogisticsQueryService._calculate_dn_age(r)
            if age_days >= threshold_days:
                critical_dns.append({
                    "dn_no": r.dn_no,
                    "dealer": r.customer_name,
                    "city": r.ship_to_city,
                    "warehouse": r.warehouse,
                    "age_days": age_days,
                    "quantity": float(r.dn_qty or 0),
                    "amount": float(r.dn_amount or 0)
                })
        
        # Sort by age descending and remove duplicates by DN
        seen_dns = set()
        unique_critical = []
        for dn in sorted(critical_dns, key=lambda x: x["age_days"], reverse=True):
            if dn["dn_no"] not in seen_dns:
                seen_dns.add(dn["dn_no"])
                unique_critical.append(dn)
        
        return {
            "success": True,
            "threshold_days": threshold_days,
            "total_critical": len(unique_critical),
            "critical_deliveries": unique_critical[:limit]
        }
    
    @staticmethod
    def get_top_delayed_dealers(db: Session, limit: int = 10) -> Dict[str, Any]:
        """Get top dealers by number of delayed deliveries (older than 15 days)."""
        all_records = db.query(DeliveryReport).all()
        
        # Use business rule: pending = PGI not completed
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        dealer_stats = {}
        for r in pending_records:
            age_days = LogisticsQueryService._calculate_dn_age(r)
            if age_days >= 15:  # Delayed threshold
                if not r.customer_name:
                    continue
                if r.customer_name not in dealer_stats:
                    dealer_stats[r.customer_name] = {"dns": set(), "quantity": 0, "max_age": 0}
                dealer_stats[r.customer_name]["dns"].add(r.dn_no)
                dealer_stats[r.customer_name]["quantity"] += float(r.dn_qty or 0)
                if age_days > dealer_stats[r.customer_name]["max_age"]:
                    dealer_stats[r.customer_name]["max_age"] = age_days
        
        sorted_dealers = sorted(
            dealer_stats.items(),
            key=lambda x: len(x[1]["dns"]),
            reverse=True
        )[:limit]
        
        return {
            "success": True,
            "count": len(sorted_dealers),
            "dealers": [
                {
                    "rank": i + 1,
                    "dealer": d[0],
                    "delayed_dns": len(d[1]["dns"]),
                    "delayed_quantity": d[1]["quantity"],
                    "max_delay_days": d[1]["max_age"]
                }
                for i, d in enumerate(sorted_dealers)
            ]
        }
    
    @staticmethod
    def generate_risk_summary(db: Session) -> Dict[str, Any]:
        """Generate executive risk summary for deliveries."""
        all_records = db.query(DeliveryReport).all()
        
        # Use business rule: pending = PGI not completed
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        # Count by age categories
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
        
        # Warehouse with highest aging backlog
        warehouse_aging = LogisticsQueryService.get_warehouse_aging(db, 1)
        top_aging_warehouse = warehouse_aging.get("warehouses", [{}])[0].get("warehouse", "N/A")
        
        # Dealer with highest delayed quantity
        top_delayed = LogisticsQueryService.get_top_delayed_dealers(db, 1)
        top_delayed_dealer = top_delayed.get("dealers", [{}])[0].get("dealer", "N/A")
        top_delayed_dns = top_delayed.get("dealers", [{}])[0].get("delayed_dns", 0)
        
        # City with highest aging
        city_aging = LogisticsQueryService.get_city_aging(db, 1)
        top_aging_city = city_aging.get("cities", [{}])[0].get("city", "N/A")
        top_aging_city_age = city_aging.get("cities", [{}])[0].get("oldest_age_days", 0)
        
        risk_summary = f"""🚨 EXECUTIVE RISK SUMMARY

📊 DELAY STATISTICS:
• {age_counts['>15']} DNs older than 15 days
• {age_counts['>30']} DNs older than 30 days
• Oldest DN: {oldest_dn} ({oldest_age} days) - {oldest_dealer}

🏭 HIGHEST AGING BACKLOG:
• Warehouse: {top_aging_warehouse}
• City: {top_aging_city} ({top_aging_city_age} days)
• Dealer: {top_delayed_dealer} ({top_delayed_dns} delayed DNs)

🎯 RECOMMENDATIONS:
• Prioritize clearance from {top_aging_warehouse}
• Follow up with {top_delayed_dealer} for pending deliveries
• Focus on {top_aging_city} for delivery performance"""
        
        return {
            "success": True,
            "risk_summary": risk_summary,
            "dns_over_15_days": age_counts[">15"],
            "dns_over_30_days": age_counts[">30"],
            "oldest_dn": oldest_dn,
            "oldest_age_days": oldest_age,
            "oldest_dealer": oldest_dealer,
            "highest_aging_warehouse": top_aging_warehouse,
            "highest_aging_city": top_aging_city,
            "highest_aging_city_age": top_aging_city_age,
            "top_delayed_dealer": top_delayed_dealer,
            "top_delayed_dns": top_delayed_dns
        }
    
    @staticmethod
    def get_sla_breach_deliveries(db: Session) -> Dict[str, Any]:
        """Get deliveries breaching SLA based on age."""
        all_records = db.query(DeliveryReport).all()
        
        # Use business rule: pending = PGI not completed
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        sla_breaches = {
            "Green (0-3 Days)": [],
            "Yellow (4-7 Days)": [],
            "Orange (8-15 Days)": [],
            "Red (15+ Days)": []
        }
        
        for r in pending_records:
            age_days = LogisticsQueryService._calculate_dn_age(r)
            dn_info = {
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "city": r.ship_to_city,
                "warehouse": r.warehouse,
                "age_days": age_days,
                "quantity": float(r.dn_qty or 0),
                "amount": float(r.dn_amount or 0)
            }
            
            if age_days <= 3:
                sla_breaches["Green (0-3 Days)"].append(dn_info)
            elif age_days <= 7:
                sla_breaches["Yellow (4-7 Days)"].append(dn_info)
            elif age_days <= 15:
                sla_breaches["Orange (8-15 Days)"].append(dn_info)
            else:
                sla_breaches["Red (15+ Days)"].append(dn_info)
        
        # Remove duplicates by DN
        for category in sla_breaches:
            seen = set()
            unique_list = []
            for item in sla_breaches[category]:
                if item["dn_no"] not in seen:
                    seen.add(item["dn_no"])
                    unique_list.append(item)
            sla_breaches[category] = unique_list
        
        return {
            "success": True,
            "sla_breaches": sla_breaches,
            "summary": {
                "green": len(sla_breaches["Green (0-3 Days)"]),
                "yellow": len(sla_breaches["Yellow (4-7 Days)"]),
                "orange": len(sla_breaches["Orange (8-15 Days)"]),
                "red": len(sla_breaches["Red (15+ Days)"])
            }
        }
    
    @staticmethod
    def get_pending_older_than(db: Session, days: int) -> Dict[str, Any]:
        """Get pending deliveries older than specified days."""
        all_records = db.query(DeliveryReport).all()
        
        # Use business rule: pending = PGI not completed
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        older_than = []
        for r in pending_records:
            age_days = LogisticsQueryService._calculate_dn_age(r)
            if age_days > days:
                older_than.append({
                    "dn_no": r.dn_no,
                    "dealer": r.customer_name,
                    "city": r.ship_to_city,
                    "warehouse": r.warehouse,
                    "age_days": age_days,
                    "quantity": float(r.dn_qty or 0),
                    "amount": float(r.dn_amount or 0)
                })
        
        # Remove duplicates by DN
        seen_dns = set()
        unique_list = []
        for item in older_than:
            if item["dn_no"] not in seen_dns:
                seen_dns.add(item["dn_no"])
                unique_list.append(item)
        
        # Sort by age descending
        unique_list.sort(key=lambda x: x["age_days"], reverse=True)
        
        return {
            "success": True,
            "days_threshold": days,
            "total_count": len(unique_list),
            "deliveries": unique_list
        }
    
    # ======================================================
    # DEALER FUNCTIONS
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
        """Get complete summary for a specific dealer with warehouse and city breakdown."""
        records = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
            .all()
        )
        
        if not records:
            return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
        
        # Use business rule for pending (PGI not completed)
        unique_dns = LogisticsQueryService._get_unique_dns(records)
        total_dns = len(unique_dns)
        
        # Delivered: PGI Completed AND POD Received
        delivered_records = [r for r in records if r.pgi_status == "Completed" and r.pod_status == "Received"]
        delivered_dns = len(LogisticsQueryService._get_unique_dns(delivered_records))
        
        # Pending: PGI not completed
        pending_records = [r for r in records if r.pgi_status != "Completed"]
        pending_dns = len(LogisticsQueryService._get_unique_dns(pending_records))
        
        # Delivered but not acknowledged
        delivered_not_ack_records = [r for r in records if r.pgi_status == "Completed" and r.pod_status == "Pending"]
        delivered_not_ack_dns = len(LogisticsQueryService._get_unique_dns(delivered_not_ack_records))
        
        # Quantity calculations
        total_qty = sum(r.dn_qty or 0 for r in records)
        delivered_qty = sum(r.dn_qty or 0 for r in delivered_records)
        pending_qty = sum(r.dn_qty or 0 for r in pending_records)
        
        # Amount calculations
        total_amount = sum(r.dn_amount or 0 for r in records)
        pending_amount = sum(r.dn_amount or 0 for r in pending_records)
        
        # Warehouse breakdown
        warehouse_stats = {}
        for r in pending_records:
            if r.warehouse:
                if r.warehouse not in warehouse_stats:
                    warehouse_stats[r.warehouse] = {"dns": set(), "quantity": 0}
                warehouse_stats[r.warehouse]["dns"].add(r.dn_no)
                warehouse_stats[r.warehouse]["quantity"] += float(r.dn_qty or 0)
        
        warehouse_breakdown = [
            {"warehouse": w, "pending_dns": len(stats["dns"]), "pending_quantity": stats["quantity"]}
            for w, stats in warehouse_stats.items()
        ]
        
        # City breakdown
        city_stats = {}
        for r in pending_records:
            if r.ship_to_city:
                if r.ship_to_city not in city_stats:
                    city_stats[r.ship_to_city] = {"dns": set(), "quantity": 0}
                city_stats[r.ship_to_city]["dns"].add(r.dn_no)
                city_stats[r.ship_to_city]["quantity"] += float(r.dn_qty or 0)
        
        city_breakdown = [
            {"city": c, "pending_dns": len(stats["dns"]), "pending_quantity": stats["quantity"]}
            for c, stats in city_stats.items()
        ]
        
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
            "pending_amount": float(pending_amount),
            "warehouse_breakdown": sorted(warehouse_breakdown, key=lambda x: x["pending_dns"], reverse=True),
            "city_breakdown": sorted(city_breakdown, key=lambda x: x["pending_dns"], reverse=True)
        }
    
    @staticmethod
    def get_dealer_dn_breakdown(db: Session, dealer_name: str) -> Dict[str, Any]:
        """Get DN breakdown for a specific dealer with products and POD status."""
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
                if r.pgi_status == "Completed" and r.pod_status == "Received":
                    status_text = "Delivered and Acknowledged"
                elif r.pgi_status == "Completed" and r.pod_status == "Pending":
                    status_text = "Delivered, Awaiting Acknowledgement"
                elif r.pgi_status == "Pending":
                    status_text = "Pending Dispatch"
                else:
                    status_text = r.delivery_status or "Unknown"
                
                dn_groups[dn_no] = {
                    "dn_no": dn_no,
                    "status": status_text,
                    "pod_status": "Received" if r.pod_status == "Received" else "Pending",
                    "total_quantity": 0,
                    "total_amount": 0,
                    "age_days": LogisticsQueryService._calculate_dn_age(r),
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
        """Get product-wise breakdown for a dealer."""
        records = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
            .all()
        )
        
        if not records:
            return {"success": False, "message": f"No products found for dealer '{dealer_name}'"}
        
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
            if r.pgi_status != "Completed":
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
        """Get product breakdown for a single DN with POD status and aging."""
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
    # HIGHEST PENDING QUERIES
    # ======================================================
    
    @staticmethod
    def get_highest_pending_dealer(db: Session) -> Dict[str, Any]:
        """Get dealer with highest number of pending DNs."""
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
        if not pending_records:
            return {"success": False, "message": "No pending deliveries found"}
        
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
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
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
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
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
    
    @staticmethod
    def get_top_pending_warehouses(db: Session, limit: int = 10) -> Dict[str, Any]:
        """Get top warehouses by pending DNs."""
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
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
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
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
        all_records = db.query(DeliveryReport).all()
        pending_records = [r for r in all_records if r.pgi_status != "Completed"]
        
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
    
    # ======================================================
    # POD PENDING QUERIES
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
    # DELIVERY COMPLETION QUERIES
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
    
    @staticmethod
    def get_pending_deliveries(db: Session, limit: int = 100):
        """Get pending deliveries using business rule (PGI not completed)."""
        rows = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status != "Completed"
        ).all()
        
        dn_groups = {}
        for r in rows:
            if r.dn_no not in dn_groups:
                dn_groups[r.dn_no] = {"amount": 0, "quantity": 0, "age_days": LogisticsQueryService._calculate_dn_age(r)}
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
    
    # ======================================================
    # BASE METHODS
    # ======================================================
    
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
    
    @staticmethod
    def get_delivery_insights(db: Session) -> Dict[str, Any]:
        """Get comprehensive delivery analytics with unique DN counting."""
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
    
    # ======================================================
    # EXECUTIVE SUMMARY (ENHANCED)
    # ======================================================
    
    @staticmethod
    def get_executive_summary(db: Session) -> Dict[str, Any]:
        """Get executive-level dashboard summary with all key KPIs."""
        insights = LogisticsQueryService.get_delivery_insights(db)
        top_dealers = LogisticsQueryService.get_top_dealers(db, 5)
        top_cities = LogisticsQueryService.get_top_cities(db, 5)
        top_pending_warehouses = LogisticsQueryService.get_top_pending_warehouses(db, 5)
        top_pending_cities = LogisticsQueryService.get_top_pending_cities(db, 5)
        top_pending_dealers = LogisticsQueryService.get_top_pending_dealers(db, 5)
        highest_pending_dealer = LogisticsQueryService.get_highest_pending_dealer(db)
        highest_pending_warehouse = LogisticsQueryService.get_highest_pending_warehouse(db)
        highest_pending_city = LogisticsQueryService.get_highest_pending_city(db)
        
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
• Warehouse: {highest_pending_warehouse.get('warehouse', 'N/A')} ({highest_pending_warehouse.get('pending_count', 0)} DNs)
• City: {highest_pending_city.get('city', 'N/A')} ({highest_pending_city.get('pending_count', 0)} DNs)

📊 TOP 5 PENDING DEALERS:
""" + "\n".join([f"   {i+1}. {d['dealer']}: {d['pending_dns']} DNs, {d['pending_quantity']:.0f} units" 
                for i, d in enumerate(top_pending_dealers.get('dealers', [])[:5])]) + """

🏭 TOP 5 PENDING WAREHOUSES:
""" + "\n".join([f"   {i+1}. {w['warehouse']}: {w['pending_dns']} DNs, {w['pending_quantity']:.0f} units" 
                for i, w in enumerate(top_pending_warehouses.get('warehouses', [])[:5])]) + """

🌆 TOP 5 PENDING CITIES:
""" + "\n".join([f"   {i+1}. {c['city']}: {c['pending_dns']} DNs, {c['pending_quantity']:.0f} units" 
                for i, c in enumerate(top_pending_cities.get('cities', [])[:5])]) + """

🎯 RECOMMENDATIONS:
• Focus on clearing pending dispatches from {highest_pending_warehouse.get('warehouse', 'warehouses')}
• Follow up with {highest_pending_dealer.get('dealer', 'dealers')} for pending acknowledgements
• Monitor {highest_pending_city.get('city', 'cities')} for delivery performance"""
        
        return {
            "success": True,
            "executive_summary": executive_summary,
            **insights,
            "top_dealers": top_dealers["records"],
            "top_cities": top_cities,
            "top_pending_warehouses": top_pending_warehouses.get("warehouses", []),
            "top_pending_cities": top_pending_cities.get("cities", []),
            "top_pending_dealers": top_pending_dealers.get("dealers", []),
            "highest_pending_dealer": highest_pending_dealer,
            "highest_pending_warehouse": highest_pending_warehouse,
            "highest_pending_city": highest_pending_city
        }
    
    # ======================================================
    # AI CONTEXT BUILDER (UPDATED WITH AGING INTENTS)
    # ======================================================
    
    @staticmethod
    def generate_ai_context(question: str, db: Session) -> Dict[str, Any]:
        """Generate rich AI context from natural language question."""
        intent_result = LogisticsQueryService.detect_intent(question)
        intent = intent_result["intent"]
        
        if intent == "dn_lookup":
            result = LogisticsQueryService.get_dn_product_breakdown(db, intent_result["dn_no"])
            result["summary"] = LogisticsQueryService.generate_dn_summary(result)
        
        elif intent == "pending_delivery_aging":
            result = LogisticsQueryService.get_pending_delivery_aging(db)
            if result.get("success") and result.get("aging_breakdown"):
                breakdown = result["aging_breakdown"]
                summary = f"📊 PENDING DELIVERY AGING REPORT\n\n"
                summary += f"Total Pending DNs: {result['total_pending_dns']}\n\n"
                for category, data in breakdown.items():
                    if len(data["dns"]) > 0:
                        summary += f"• {category}: {len(data['dns'])} DNs, {data['quantity']:.0f} units\n"
                result["summary"] = summary
            else:
                result["summary"] = result.get("message", "No pending deliveries found")
        
        elif intent == "dealer_aging":
            result = LogisticsQueryService.get_dealer_aging(db, intent_result.get("dealer_name", ""))
            if result.get("success"):
                breakdown = result.get("aging_breakdown", {})
                summary = f"📊 DEALER AGING SUMMARY: {result['dealer_name']}\n\n"
                for category, data in breakdown.items():
                    if len(data["dns"]) > 0:
                        summary += f"• {category}: {len(data['dns'])} DNs, {data['quantity']:.0f} units\n"
                summary += f"\n⚠️ Oldest Pending DN: {result.get('oldest_dn', 'N/A')} ({result.get('oldest_age_days', 0)} days)"
                result["summary"] = summary
            else:
                result["summary"] = result.get("message", f"No data found for dealer '{intent_result.get('dealer_name', '')}'")
        
        elif intent == "warehouse_aging":
            result = LogisticsQueryService.get_warehouse_aging(db, 10)
            if result.get("success") and result.get("warehouses"):
                summary = "🏭 WAREHOUSE AGING RANKING\n\n"
                for w in result["warehouses"]:
                    summary += f"{w['rank']}. {w['warehouse']}\n"
                    summary += f"   Pending DNs: {w['pending_dns']}, Pending Units: {w['pending_quantity']:.0f}\n"
                    summary += f"   Oldest DN: {w['oldest_dn']} ({w['oldest_age_days']} days)\n\n"
                result["summary"] = summary
            else:
                result["summary"] = "No warehouse aging data available"
        
        elif intent == "city_aging":
            result = LogisticsQueryService.get_city_aging(db, 10)
            if result.get("success") and result.get("cities"):
                summary = "🌆 CITY AGING RANKING\n\n"
                for c in result["cities"]:
                    summary += f"{c['rank']}. {c['city']}\n"
                    summary += f"   Pending DNs: {c['pending_dns']}, Pending Units: {c['pending_quantity']:.0f}\n"
                    summary += f"   Oldest DN: {c['oldest_dn']} ({c['oldest_age_days']} days)\n\n"
                result["summary"] = summary
            else:
                result["summary"] = "No city aging data available"
        
        elif intent == "critical_pending_dns":
            result = LogisticsQueryService.get_critical_pending_dns(db, 15, 20)
            if result.get("success") and result.get("critical_deliveries"):
                summary = "🚨 CRITICAL DELIVERIES (15+ days)\n\n"
                for dn in result["critical_deliveries"][:10]:
                    summary += f"• DN {dn['dn_no']}: {dn['dealer']}\n"
                    summary += f"   Age: {dn['age_days']} days, Quantity: {dn['quantity']:.0f} units\n"
                if len(result["critical_deliveries"]) > 10:
                    summary += f"\nAnd {len(result['critical_deliveries']) - 10} more critical deliveries"
                result["summary"] = summary
            else:
                result["summary"] = "No critical deliveries found"
        
        elif intent == "risk_summary":
            result = LogisticsQueryService.generate_risk_summary(db)
            result["summary"] = result.get("risk_summary", "Risk summary generated")
        
        elif intent == "top_delayed_dealers":
            result = LogisticsQueryService.get_top_delayed_dealers(db, 10)
            if result.get("success") and result.get("dealers"):
                summary = "🏆 TOP DELAYED DEALERS (15+ days)\n\n"
                for d in result["dealers"]:
                    summary += f"{d['rank']}. {d['dealer']}: {d['delayed_dns']} DNs, {d['delayed_quantity']:.0f} units\n"
                    summary += f"   Max Delay: {d['max_delay_days']} days\n\n"
                result["summary"] = summary
            else:
                result["summary"] = "No delayed dealers found"
        
        elif intent == "sla_breach_deliveries":
            result = LogisticsQueryService.get_sla_breach_deliveries(db)
            if result.get("success"):
                summary = "🔴 SLA BREACH DELIVERIES\n\n"
                summary += f"• Green (0-3 days): {result['summary']['green']} DNs\n"
                summary += f"• Yellow (4-7 days): {result['summary']['yellow']} DNs\n"
                summary += f"• Orange (8-15 days): {result['summary']['orange']} DNs\n"
                summary += f"• Red (15+ days): {result['summary']['red']} DNs\n\n"
                
                if result['sla_breaches']['Red (15+ Days)']:
                    summary += "⚠️ RED ALERT (15+ days):\n"
                    for dn in result['sla_breaches']['Red (15+ Days)'][:5]:
                        summary += f"   • DN {dn['dn_no']}: {dn['dealer']} ({dn['age_days']} days)\n"
                result["summary"] = summary
            else:
                result["summary"] = "No SLA data available"
        
        elif intent == "pending_older_than":
            days = intent_result.get("days", 15)
            result = LogisticsQueryService.get_pending_older_than(db, days)
            if result.get("success"):
                summary = f"📋 PENDING DELIVERIES OLDER THAN {days} DAYS\n\n"
                for dn in result["deliveries"][:15]:
                    summary += f"• DN {dn['dn_no']}: {dn['dealer']} - {dn['age_days']} days\n"
                    summary += f"   Quantity: {dn['quantity']:.0f} units, Amount: Rs {dn['amount']:,.2f}\n"
                if len(result["deliveries"]) > 15:
                    summary += f"\nAnd {len(result['deliveries']) - 15} more deliveries"
                result["summary"] = summary
            else:
                result["summary"] = f"No deliveries older than {days} days found"
        
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
        
        elif intent == "highest_pending_city":
            result = LogisticsQueryService.get_highest_pending_city(db)
            if result.get("success"):
                result["summary"] = (f"{result['city']} currently has the highest pending deliveries "
                                    f"with {result['pending_count']} pending DNs, {result['pending_quantity']:.0f} units, "
                                    f"totaling Rs {result['pending_amount']:,.2f}.")
            else:
                result["summary"] = "No pending deliveries found in the system."
        
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
    # AI SUMMARY GENERATORS
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
        pod_status = dn_result.get("pod_status", "Pending")
        age_days = dn_result.get("age_days", 0)
        total_amount = dn_result.get("total_amount", 0)
        total_quantity = dn_result.get("total_quantity", 0)
        products = dn_result.get("products", [])
        
        summary = f"🔹 DN {dn_no} belongs to {dealer} in {city}.\n\n"
        summary += f"📋 Status: {status}\n"
        summary += f"📋 POD: {pod_status}\n"
        summary += f"📋 Age: {age_days} days\n"
        summary += f"🏭 Warehouse: {warehouse}\n"
        summary += f"📦 Total Quantity: {total_quantity:.0f} units\n"
        summary += f"💰 Amount: Rs {total_amount:,.2f}\n\n"
        
        if products:
            summary += "📦 Products:\n"
            for p in products[:5]:
                summary += f"   • {p['product_name']}: {p['quantity']:.0f} units\n"
            if len(products) > 5:
                summary += f"   • And {len(products) - 5} more products\n"
        
        if age_days > 15:
            summary += "\n⚠️ This delivery is critical and requires immediate attention."
        
        return summary
    
    @staticmethod
    def generate_dealer_summary_text(result: Dict[str, Any]) -> str:
        """Generate natural language dealer summary with warehouse and city breakdown."""
        text = f"📊 DEALER SUMMARY: {result['dealer_name']}\n\n"
        text += f"📦 Deliveries:\n"
        text += f"• Total DNs: {result['total_dns']}\n"
        text += f"• Delivered: {result['delivered_dns']}\n"
        text += f"• Pending: {result['pending_dns']}\n"
        text += f"• Awaiting Acknowledgement: {result['delivered_not_acknowledged']}\n\n"
        text += f"📦 Quantity:\n"
        text += f"• Total Units: {result['total_quantity']:.0f}\n"
        text += f"• Delivered: {result['delivered_quantity']:.0f}\n"
        text += f"• Pending: {result['pending_quantity']:.0f}\n\n"
        text += f"💰 Amount:\n"
        text += f"• Total Value: Rs {result['total_amount']:,.2f}\n"
        text += f"• Pending Value: Rs {result['pending_amount']:,.2f}\n\n"
        
        if result.get("warehouse_breakdown"):
            text += f"🏭 Warehouse Breakdown (Pending):\n"
            for w in result["warehouse_breakdown"][:5]:
                text += f"   • {w['warehouse']}: {w['pending_dns']} DNs, {w['pending_quantity']:.0f} units\n"
            text += "\n"
        
        if result.get("city_breakdown"):
            text += f"🌆 City Breakdown (Pending):\n"
            for c in result["city_breakdown"][:5]:
                text += f"   • {c['city']}: {c['pending_dns']} DNs, {c['pending_quantity']:.0f} units\n"
        
        return text
    
    @staticmethod
    def generate_dealer_dn_breakdown_text(result: Dict[str, Any]) -> str:
        """Generate summary for dealer DN breakdown."""
        dealer_name = result.get("dealer_name", "Unknown")
        dns = result.get("dns", [])
        
        if not dns:
            return f"No DNs found for {dealer_name}"
        
        text = f"📋 DN BREAKDOWN FOR {dealer_name}:\n\n"
        for dn in dns[:5]:
            text += f"🔹 DN {dn['dn_no']}: {dn['status']}\n"
            text += f"   POD: {dn['pod_status']}\n"
            text += f"   Age: {dn.get('age_days', 0)} days\n"
            text += f"   Total Units: {dn['total_quantity']:.0f}\n"
            for product in dn['products'][:3]:
                text += f"   • {product['product_name']}: {product['quantity']:.0f} units\n"
            if len(dn['products']) > 3:
                text += f"   • And {len(dn['products']) - 3} more products\n"
            text += "\n"
        
        if len(dns) > 5:
            text += f"And {len(dns) - 5} more DNs.\n"
        
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
                f"These deliveries are pending dispatch from warehouses.")
    
    @staticmethod
    def generate_city_summary(result: Dict[str, Any]) -> str:
        """Generate summary for city search."""
        city = result.get("city", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for {city}."
        
        return f"Found {count} deliveries for {city}."
    
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
- DN Age > 15 days = Critical
- DN Age > 30 days = Urgent

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
