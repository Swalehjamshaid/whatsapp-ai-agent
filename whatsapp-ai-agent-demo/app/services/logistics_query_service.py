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
    # INTENT DETECTION (ENHANCED)
    # ======================================================
    
    @staticmethod
    def detect_intent(question: str) -> Dict[str, Any]:
        """
        Detect user intent from natural language question.
        
        Examples:
        - "6243612322" -> {"intent": "dn_lookup", "dn_no": "6243612322"}
        - "What is status of DN 6243612322?" -> {"intent": "dn_lookup", "dn_no": "6243612322"}
        - "How many pending deliveries?" -> {"intent": "pending_deliveries"}
        - "Show Lahore deliveries" -> {"intent": "city_search", "city": "Lahore"}
        - "Which dealer has the highest pending?" -> {"intent": "highest_pending_dealer"}
        """
        question_lower = question.lower().strip()
        
        # DN detection with keywords
        dn_keywords = ["dn", "delivery note", "delivery number", "status", "check", "show"]
        
        # Check for DN number (numeric string of 8-15 digits)
        dn_match = re.search(r'\b(\d{8,15})\b', question)
        if dn_match:
            return {
                "intent": "dn_lookup",
                "dn_no": dn_match.group(1)
            }
        
        # Also check for DN with keywords
        for keyword in dn_keywords:
            if keyword in question_lower:
                dn_match_with_keyword = re.search(r'\b(\d{8,15})\b', question)
                if dn_match_with_keyword:
                    return {
                        "intent": "dn_lookup",
                        "dn_no": dn_match_with_keyword.group(1)
                    }
        
        # ==================================================
        # NEW: Highest pending dealer/warehouse/city intents
        # ==================================================
        
        if any(phrase in question_lower for phrase in [
            "highest pending dealer", "dealer has highest pending", 
            "top pending dealer", "which dealer has the highest pending",
            "dealer with most pending", "largest pending dealer"
        ]):
            return {"intent": "highest_pending_dealer"}
        
        if any(phrase in question_lower for phrase in [
            "highest pending warehouse", "warehouse has highest pending",
            "top pending warehouse", "which warehouse has the highest pending",
            "warehouse with most pending", "largest pending warehouse"
        ]):
            return {"intent": "highest_pending_warehouse"}
        
        if any(phrase in question_lower for phrase in [
            "highest pending city", "city has highest pending",
            "top pending city", "which city has the highest pending",
            "city with most pending", "largest pending city"
        ]):
            return {"intent": "highest_pending_city"}
        
        # ==================================================
        # NEW: Delivery completion and acknowledgment intents
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
            "pod pending but pgi completed", "delivered pod pending"
        ]):
            return {"intent": "delivered_not_acknowledged"}
        
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
        
        # Executive/CEO queries
        executive_keywords = ["ceo", "executive", "performance", "report", "logistics summary", "dashboard overview"]
        if any(keyword in question_lower for keyword in executive_keywords):
            return {"intent": "executive_summary"}
        
        # Pending deliveries intent
        pending_keywords = ["pending delivery", "pending deliveries", "how many pending", "pending orders", "undelivered", "not delivered"]
        if any(keyword in question_lower for keyword in pending_keywords):
            return {"intent": "pending_deliveries"}
        
        # Pending POD intent
        pod_keywords = ["pending pod", "pod pending", "delivery proof pending", "signature pending", "proof of delivery pending"]
        if any(keyword in question_lower for keyword in pod_keywords):
            return {"intent": "pending_pod"}
        
        # Pending PGI intent
        pgi_keywords = ["pending pgi", "pgi pending", "goods issue pending", "not dispatched", "warehouse pending"]
        if any(keyword in question_lower for keyword in pgi_keywords):
            return {"intent": "pending_pgi"}
        
        # City detection for multi-word cities
        city_patterns = [
            r'in\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
            r'for\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
            r'at\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
            r'deliveries?\s+in\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
            r'city\s+([a-zA-Z\s]+?)(?:\s+only|\s+$|\.|\?|$)'
        ]
        
        for pattern in city_patterns:
            city_match = re.search(pattern, question_lower)
            if city_match:
                city = city_match.group(1).strip().title()
                if city and len(city) > 2:
                    return {"intent": "city_search", "city": city}
        
        # Dealer/customer search
        dealer_keywords = ["dealer", "customer", "show me deliveries for", "deliveries of"]
        for keyword in dealer_keywords:
            if keyword in question_lower:
                parts = question_lower.split(keyword)
                if len(parts) > 1:
                    dealer_name = parts[1].strip().title()
                    if dealer_name and len(dealer_name) > 2:
                        return {"intent": "dealer_search", "dealer_name": dealer_name}
        
        # Material detection
        material_patterns = [
            r'material[\s#:]*([a-zA-Z0-9]+)',
            r'material\s+no[\s#:]*([a-zA-Z0-9]+)',
            r'material\s+number[\s#:]*([a-zA-Z0-9]+)',
            r'product[\s#:]*([a-zA-Z0-9]+)',
            r'sku[\s#:]*([a-zA-Z0-9]+)'
        ]
        
        for pattern in material_patterns:
            material_match = re.search(pattern, question_lower)
            if material_match:
                return {
                    "intent": "material_search",
                    "material_no": material_match.group(1).upper()
                }
        
        # Warehouse search
        warehouse_keywords = ["warehouse", "stock from", "from warehouse", "godown"]
        for keyword in warehouse_keywords:
            if keyword in question_lower:
                parts = question_lower.split(keyword)
                if len(parts) > 1:
                    warehouse = parts[1].strip().upper()
                    if warehouse and len(warehouse) >= 2:
                        return {"intent": "warehouse_search", "warehouse": warehouse}
        
        # Division search
        division_match = re.search(r'division\s+([a-zA-Z0-9]+)', question_lower)
        if division_match:
            return {
                "intent": "division_search",
                "division": division_match.group(1).upper()
            }
        
        # Summary/insights intent
        summary_keywords = ["summary", "dashboard", "overview", "report", "statistics", "insights", "analytics", "how many"]
        if any(keyword in question_lower for keyword in summary_keywords):
            return {"intent": "dashboard_summary"}
        
        # Top dealers/cities intent
        if "top" in question_lower:
            if "dealer" in question_lower or "customer" in question_lower:
                return {"intent": "top_dealers"}
            if "city" in question_lower:
                return {"intent": "top_cities"}
        
        # Default fallback
        return {"intent": "general_query", "question": question}
    
    # ======================================================
    # HELPER: Convert SQLAlchemy objects to dictionaries
    # ======================================================
    
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
    # NEW: HIGHEST PENDING QUERIES
    # ======================================================
    
    @staticmethod
    def get_highest_pending_dealer(db: Session) -> Dict[str, Any]:
        """Get dealer with highest number of pending deliveries."""
        result = (
            db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.id).label("pending_count"),
                func.sum(DeliveryReport.dn_amount).label("pending_amount")
            )
            .filter(DeliveryReport.pending_flag.is_(True))
            .filter(DeliveryReport.customer_name.isnot(None))
            .group_by(DeliveryReport.customer_name)
            .order_by(func.count(DeliveryReport.id).desc())
            .first()
        )
        
        if not result or not result.customer_name:
            return {
                "success": False,
                "message": "No pending deliveries found"
            }
        
        return {
            "success": True,
            "dealer": result.customer_name,
            "pending_count": result.pending_count,
            "pending_amount": float(result.pending_amount or 0)
        }
    
    @staticmethod
    def get_highest_pending_warehouse(db: Session) -> Dict[str, Any]:
        """Get warehouse with highest number of pending deliveries."""
        result = (
            db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.id).label("pending_count"),
                func.sum(DeliveryReport.dn_amount).label("pending_amount")
            )
            .filter(DeliveryReport.pending_flag.is_(True))
            .filter(DeliveryReport.warehouse.isnot(None))
            .group_by(DeliveryReport.warehouse)
            .order_by(func.count(DeliveryReport.id).desc())
            .first()
        )
        
        if not result or not result.warehouse:
            return {
                "success": False,
                "message": "No pending deliveries found"
            }
        
        return {
            "success": True,
            "warehouse": result.warehouse,
            "pending_count": result.pending_count,
            "pending_amount": float(result.pending_amount or 0)
        }
    
    @staticmethod
    def get_highest_pending_city(db: Session) -> Dict[str, Any]:
        """Get city with highest number of pending deliveries."""
        result = (
            db.query(
                DeliveryReport.ship_to_city,
                func.count(DeliveryReport.id).label("pending_count"),
                func.sum(DeliveryReport.dn_amount).label("pending_amount")
            )
            .filter(DeliveryReport.pending_flag.is_(True))
            .filter(DeliveryReport.ship_to_city.isnot(None))
            .group_by(DeliveryReport.ship_to_city)
            .order_by(func.count(DeliveryReport.id).desc())
            .first()
        )
        
        if not result or not result.ship_to_city:
            return {
                "success": False,
                "message": "No pending deliveries found"
            }
        
        return {
            "success": True,
            "city": result.ship_to_city,
            "pending_count": result.pending_count,
            "pending_amount": float(result.pending_amount or 0)
        }
    
    @staticmethod
    def get_highest_pod_pending_dealer(db: Session) -> Dict[str, Any]:
        """Get dealer with highest number of POD pending deliveries."""
        result = (
            db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.id).label("pod_pending_count"),
                func.sum(DeliveryReport.dn_amount).label("pod_pending_amount")
            )
            .filter(DeliveryReport.pod_status == "Pending")
            .filter(DeliveryReport.pgi_status == "Completed")
            .filter(DeliveryReport.customer_name.isnot(None))
            .group_by(DeliveryReport.customer_name)
            .order_by(func.count(DeliveryReport.id).desc())
            .first()
        )
        
        if not result or not result.customer_name:
            return {
                "success": False,
                "message": "No POD pending deliveries found"
            }
        
        return {
            "success": True,
            "dealer": result.customer_name,
            "pod_pending_count": result.pod_pending_count,
            "pod_pending_amount": float(result.pod_pending_amount or 0)
        }
    
    @staticmethod
    def get_highest_pod_pending_warehouse(db: Session) -> Dict[str, Any]:
        """Get warehouse with highest number of POD pending deliveries."""
        result = (
            db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.id).label("pod_pending_count"),
                func.sum(DeliveryReport.dn_amount).label("pod_pending_amount")
            )
            .filter(DeliveryReport.pod_status == "Pending")
            .filter(DeliveryReport.pgi_status == "Completed")
            .filter(DeliveryReport.warehouse.isnot(None))
            .group_by(DeliveryReport.warehouse)
            .order_by(func.count(DeliveryReport.id).desc())
            .first()
        )
        
        if not result or not result.warehouse:
            return {
                "success": False,
                "message": "No POD pending deliveries found"
            }
        
        return {
            "success": True,
            "warehouse": result.warehouse,
            "pod_pending_count": result.pod_pending_count,
            "pod_pending_amount": float(result.pod_pending_amount or 0)
        }
    
    # ======================================================
    # NEW: DELIVERY COMPLETION AND ACKNOWLEDGMENT QUERIES
    # ======================================================
    
    @staticmethod
    def get_completed_deliveries_count(db: Session) -> Dict[str, Any]:
        """Get count of completed deliveries (PGI Completed + POD Received)."""
        count = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Received"
            )
            .count()
        )
        
        total_amount = (
            db.query(func.sum(DeliveryReport.dn_amount))
            .filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Received"
            )
            .scalar() or 0
        )
        
        return {
            "success": True,
            "completed_count": count,
            "completed_amount": float(total_amount)
        }
    
    @staticmethod
    def get_acknowledged_deliveries(db: Session) -> Dict[str, Any]:
        """Get deliveries that have been acknowledged (POD Received)."""
        rows = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.pod_status == "Received")
            .all()
        )
        
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        total_amount = sum(r.get("dn_amount", 0) for r in records)
        
        return {
            "success": True,
            "acknowledged_count": len(records),
            "acknowledged_amount": float(total_amount),
            "records": records
        }
    
    @staticmethod
    def get_delivered_not_acknowledged(db: Session) -> Dict[str, Any]:
        """Get deliveries delivered (PGI Completed) but not acknowledged (POD Pending)."""
        rows = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            )
            .all()
        )
        
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        total_amount = sum(r.get("dn_amount", 0) for r in records)
        
        return {
            "success": True,
            "delivered_not_acknowledged_count": len(records),
            "delivered_not_acknowledged_amount": float(total_amount),
            "records": records
        }
    
    # ======================================================
    # AI CONTEXT BUILDER (UPDATED WITH NEW INTENTS)
    # ======================================================
    
    @staticmethod
    def generate_ai_context(question: str, db: Session) -> Dict[str, Any]:
        """
        Generate rich AI context from natural language question.
        This is the main entry point for GPT/OpenAI integration.
        """
        # Detect intent
        intent_result = LogisticsQueryService.detect_intent(question)
        intent = intent_result["intent"]
        
        # Route to appropriate handler
        if intent == "dn_lookup":
            result = LogisticsQueryService.get_dn_status(db, intent_result["dn_no"])
            result["summary"] = LogisticsQueryService.generate_dn_summary(result)
        
        elif intent == "pending_deliveries":
            result = LogisticsQueryService.get_pending_deliveries(db)
            result["summary"] = LogisticsQueryService.generate_pending_summary(result)
        
        elif intent == "pending_pod":
            result = LogisticsQueryService.get_pending_pod(db)
            result["summary"] = f"There are {result['pending_pod']} deliveries awaiting dealer acknowledgement."
        
        elif intent == "pending_pgi":
            result = LogisticsQueryService.get_pending_pgi(db)
            result["summary"] = f"There are {result['pending_pgi']} deliveries pending dispatch from warehouse."
        
        # NEW: Highest pending queries
        elif intent == "highest_pending_dealer":
            result = LogisticsQueryService.get_highest_pending_dealer(db)
            if result.get("success"):
                result["summary"] = (
                    f"{result['dealer']} currently has the highest pending deliveries "
                    f"with {result['pending_count']} pending DNs totaling Rs {result['pending_amount']:,.2f}."
                )
            else:
                result["summary"] = "No pending deliveries found in the system."
        
        elif intent == "highest_pending_warehouse":
            result = LogisticsQueryService.get_highest_pending_warehouse(db)
            if result.get("success"):
                result["summary"] = (
                    f"Warehouse {result['warehouse']} currently has the highest pending deliveries "
                    f"with {result['pending_count']} pending DNs totaling Rs {result['pending_amount']:,.2f}."
                )
            else:
                result["summary"] = "No pending deliveries found in the system."
        
        elif intent == "highest_pending_city":
            result = LogisticsQueryService.get_highest_pending_city(db)
            if result.get("success"):
                result["summary"] = (
                    f"{result['city']} currently has the highest pending deliveries "
                    f"with {result['pending_count']} pending DNs totaling Rs {result['pending_amount']:,.2f}."
                )
            else:
                result["summary"] = "No pending deliveries found in the system."
        
        # NEW: POD pending queries
        elif intent == "highest_pod_pending_dealer":
            result = LogisticsQueryService.get_highest_pod_pending_dealer(db)
            if result.get("success"):
                result["summary"] = (
                    f"{result['dealer']} has the most deliveries awaiting acknowledgement "
                    f"with {result['pod_pending_count']} DNs totaling Rs {result['pod_pending_amount']:,.2f}."
                )
            else:
                result["summary"] = "No POD pending deliveries found."
        
        elif intent == "highest_pod_pending_warehouse":
            result = LogisticsQueryService.get_highest_pod_pending_warehouse(db)
            if result.get("success"):
                result["summary"] = (
                    f"Warehouse {result['warehouse']} has the most deliveries awaiting acknowledgement "
                    f"with {result['pod_pending_count']} DNs totaling Rs {result['pod_pending_amount']:,.2f}."
                )
            else:
                result["summary"] = "No POD pending deliveries found."
        
        # NEW: Completion and acknowledgment queries
        elif intent == "completed_deliveries_count":
            result = LogisticsQueryService.get_completed_deliveries_count(db)
            result["summary"] = (
                f"There are {result['completed_count']} completed deliveries "
                f"totaling Rs {result['completed_amount']:,.2f}."
            )
        
        elif intent == "acknowledged_deliveries":
            result = LogisticsQueryService.get_acknowledged_deliveries(db)
            result["summary"] = (
                f"There are {result['acknowledged_count']} acknowledged deliveries "
                f"totaling Rs {result['acknowledged_amount']:,.2f}."
            )
        
        elif intent == "delivered_not_acknowledged":
            result = LogisticsQueryService.get_delivered_not_acknowledged(db)
            result["summary"] = (
                f"There are {result['delivered_not_acknowledged_count']} deliveries that have been dispatched "
                f"but are awaiting dealer acknowledgement, totaling Rs {result['delivered_not_acknowledged_amount']:,.2f}."
            )
        
        elif intent == "city_search":
            result = LogisticsQueryService.get_city_deliveries(db, intent_result["city"])
            result["summary"] = LogisticsQueryService.generate_city_summary(result)
        
        elif intent == "dealer_search":
            result = LogisticsQueryService.get_dealer_deliveries(db, intent_result["dealer_name"])
            result["summary"] = LogisticsQueryService.generate_dealer_summary(result)
        
        elif intent == "material_search":
            result = LogisticsQueryService.search_material(db, intent_result["material_no"])
            result["summary"] = LogisticsQueryService.generate_material_summary(result)
        
        elif intent == "warehouse_search":
            result = LogisticsQueryService.get_warehouse_deliveries(db, intent_result["warehouse"])
            result["summary"] = LogisticsQueryService.generate_warehouse_summary(result)
        
        elif intent == "division_search":
            result = LogisticsQueryService.get_division_deliveries(db, intent_result["division"])
            result["summary"] = LogisticsQueryService.generate_division_summary(result)
        
        elif intent == "dashboard_summary":
            result = LogisticsQueryService.get_delivery_insights(db)
            result["summary"] = LogisticsQueryService.generate_insights_summary(result)
        
        elif intent == "executive_summary":
            result = LogisticsQueryService.get_executive_summary(db)
            result["summary"] = result.get("executive_summary", "Executive summary generated successfully.")
        
        elif intent == "top_dealers":
            result = LogisticsQueryService.get_top_dealers(db)
            result["summary"] = f"Top dealers: {', '.join([d['dealer_name'] for d in result['records'][:5]])}"
        
        elif intent == "top_cities":
            result = LogisticsQueryService.get_top_cities(db)
            result["summary"] = f"Top cities by delivery volume: {', '.join([c['city'] for c in result[:5]])}"
        
        else:
            # Return general dashboard for unknown queries
            result = LogisticsQueryService.get_delivery_insights(db)
            result["summary"] = "Here's the current logistics dashboard summary."
        
        # Add question and intent to result
        result["question"] = question
        result["intent"] = intent
        
        return result
    
    # ======================================================
    # AI SUMMARY GENERATORS (UPDATED WITH BUSINESS RULES)
    # ======================================================
    
    @staticmethod
    def generate_dn_summary(dn_result: Dict[str, Any]) -> str:
        """Generate natural language summary for DN lookup with business rules."""
        if not dn_result.get("success"):
            return f"DN {dn_result.get('dn_no', 'unknown')} was not found in the system."
        
        records = dn_result.get("records", [])
        if not records:
            return f"No records found for DN {dn_result.get('dn_no', 'unknown')}."
        
        # Get first record for main details
        main = records[0]
        customer = main.get("customer_name", "Unknown Customer")
        city = main.get("city", "Unknown City")
        warehouse = main.get("warehouse", "Unknown Warehouse")
        pgi_status = main.get("pgi_status", "Unknown")
        pod_status = main.get("pod_status", "Unknown")
        amount = main.get("dn_amount", 0)
        
        summary = f"DN {dn_result['dn_no']} belongs to {customer} in {city}. "
        
        # FIXED: Business rules for PGI/POD
        if pgi_status == "Completed" and pod_status == "Received":
            summary += "The shipment has been delivered and acknowledged by the dealer. "
        elif pgi_status == "Completed" and pod_status == "Pending":
            summary += "The shipment has been delivered and is awaiting dealer acknowledgement. "
        elif pgi_status == "Pending":
            summary += "The shipment is pending dispatch from the warehouse. "
        else:
            summary += "The shipment status is currently being updated. "
        
        summary += f"Warehouse: {warehouse}. Amount: Rs {amount:,.2f}."
        
        if len(records) > 1:
            summary += f" There are {len(records)} line items in this delivery."
        
        return summary
    
    @staticmethod
    def generate_pending_summary(result: Dict[str, Any]) -> str:
        """Generate summary for pending deliveries."""
        count = result.get("count", 0)
        if count == 0:
            return "There are no pending deliveries. All deliveries are complete!"
        
        total_amount = result.get("total_amount", 0)
        
        return f"There are {count} pending deliveries totaling Rs {total_amount:,.2f}. These deliveries have not been completed yet."
    
    @staticmethod
    def generate_city_summary(result: Dict[str, Any]) -> str:
        """Generate summary for city search."""
        city = result.get("city", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for {city}."
        
        records = result.get("records", [])
        completed = sum(1 for r in records if r.get("pgi_status") == "Completed" and r.get("pod_status") == "Received")
        delivered_not_acknowledged = sum(1 for r in records if r.get("pgi_status") == "Completed" and r.get("pod_status") == "Pending")
        pending_dispatch = sum(1 for r in records if r.get("pgi_status") == "Pending")
        
        return (f"Found {count} deliveries for {city}. "
                f"{completed} completed and acknowledged, "
                f"{delivered_not_acknowledged} delivered awaiting acknowledgement, "
                f"{pending_dispatch} pending dispatch.")
    
    @staticmethod
    def generate_dealer_summary(result: Dict[str, Any]) -> str:
        """Generate summary for dealer search."""
        dealer = result.get("dealer_code", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for dealer {dealer}."
        
        records = result.get("records", [])
        total_amount = sum(r.get("dn_amount", 0) for r in records)
        pending_count = sum(1 for r in records if r.get("pending_flag", False))
        
        return f"Dealer {dealer} has {count} deliveries totaling Rs {total_amount:,.2f}. {pending_count} of these are pending."
    
    @staticmethod
    def generate_material_summary(result: Dict[str, Any]) -> str:
        """Generate summary for material search."""
        material = result.get("material_no", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for material {material}."
        
        total_qty = result.get("total_quantity", 0)
        
        return f"Material {material} appears in {count} deliveries with total quantity {total_qty}."
    
    @staticmethod
    def generate_warehouse_summary(result: Dict[str, Any]) -> str:
        """Generate summary for warehouse search."""
        warehouse = result.get("warehouse", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for warehouse {warehouse}."
        
        records = result.get("records", [])
        pending = sum(1 for r in records if r.get("pending_flag", False))
        
        return f"Warehouse {warehouse} has {count} deliveries, with {pending} pending."
    
    @staticmethod
    def generate_division_summary(result: Dict[str, Any]) -> str:
        """Generate summary for division search."""
        division = result.get("division", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for division {division}."
        
        return f"Division {division} has {count} deliveries."
    
    @staticmethod
    def generate_insights_summary(result: Dict[str, Any]) -> str:
        """Generate summary for delivery insights with business rules."""
        return (
            f"Logistics Summary: {result['total_records']} total deliveries. "
            f"{result['pending_deliveries']} pending (Rs {result['pending_amount']:,.2f}). "
            f"{result['pending_pgi']} pending dispatch, "
            f"{result['pending_pod']} awaiting dealer acknowledgement. "
            f"Top city: {result['top_city']}. Top warehouse: {result['top_warehouse']}."
        )
    
    # ======================================================
    # GPT PROMPT BUILDER (ENHANCED WITH BUSINESS RULES)
    # ======================================================
    
    @staticmethod
    def build_gpt_prompt(question: str, context: Dict[str, Any]) -> str:
        """
        Build a professional prompt for GPT/OpenAI.
        Includes business rules for proper logistics interpretation.
        """
        intent = context.get("intent", "general_query")
        summary = context.get("summary", "No summary available")
        
        prompt = f"""You are a professional Logistics Operations Manager for a supply chain company.

USER QUESTION:
{question}

INTENT DETECTED: {intent}

DATABASE INFORMATION:
{summary}

BUSINESS RULES FOR LOGISTICS INTERPRETATION:
1. PGI Status = "Completed" means: The shipment has been dispatched/delivered from warehouse
2. PGI Status = "Pending" means: The shipment is still at warehouse, pending dispatch
3. POD Status = "Received" means: The dealer has acknowledged and received the shipment
4. POD Status = "Pending" means: Shipment delivered but awaiting dealer acknowledgement
5. When PGI is Completed but POD is Pending: State "Shipment delivered and awaiting dealer acknowledgement"
6. When POD is Received: State "Dealer has received and acknowledged the shipment"
7. When PGI is Pending: State "Shipment is pending dispatch from warehouse"
8. Never expose raw database field names (PGI/POD) to end users
9. Always use business-friendly terms: "dispatched", "delivered", "acknowledged", "pending"

RESPONSE GUIDELINES:
1. Act as a Logistics Operations Manager
2. Explain delivery status clearly using business terms
3. Explain business impact when relevant
4. Mention pending risks if applicable
5. Keep responses professional but conversational
6. Be concise (2-4 sentences for simple queries)
7. Format amounts as Rs X,XXX.XX
8. For pending items, suggest follow-up actions

RESPONSE STYLE:
- Professional but approachable
- Data-driven but human-readable
- Action-oriented for pending items
- Acknowledge risks and delays

RESPONSE:"""
        
        return prompt
    
    # ======================================================
    # UNIFIED AI QUERY HANDLER
    # ======================================================
    
    @staticmethod
    def handle_ai_query(question: str, db: Session, openai_client=None) -> Dict[str, Any]:
        """
        Complete AI-powered query handler for WhatsApp.
        This handles intent detection, database querying, and AI response generation.
        """
        # Step 1: Get context from database
        context = LogisticsQueryService.generate_ai_context(question, db)
        
        # Step 2: Build GPT prompt
        prompt = LogisticsQueryService.build_gpt_prompt(question, context)
        
        # Step 3: If OpenAI client provided, generate AI response
        ai_response = None
        if openai_client:
            try:
                response = openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "You are a professional Logistics Operations Manager."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                ai_response = response.choices[0].message.content
            except Exception as e:
                ai_response = f"Unable to generate AI response at this time. Error: {str(e)}"
        else:
            # Fallback to summary if no OpenAI client
            ai_response = context.get("summary", "Query processed successfully.")
        
        # Step 4: Return complete response
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
    # DELIVERY ANALYTICS (RETURNS DICTIONARIES)
    # ======================================================
    
    @staticmethod
    def get_delivery_insights(db: Session) -> Dict[str, Any]:
        """Get comprehensive delivery analytics."""
        
        total_records = db.query(DeliveryReport).count()
        
        pending_deliveries = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).count()
        
        pending_pod = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending"
        ).count()
        
        pending_pgi = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Pending"
        ).count()
        
        pending_amount = db.query(
            func.sum(DeliveryReport.dn_amount)
        ).filter(
            DeliveryReport.pending_flag.is_(True)
        ).scalar() or 0
        
        # Top warehouse
        top_warehouse_row = db.query(
            DeliveryReport.warehouse,
            func.count(DeliveryReport.id).label("count")
        ).filter(
            DeliveryReport.warehouse.isnot(None)
        ).group_by(
            DeliveryReport.warehouse
        ).order_by(
            func.count(DeliveryReport.id).desc()
        ).first()
        
        # Top city
        top_city_row = db.query(
            DeliveryReport.ship_to_city,
            func.count(DeliveryReport.id).label("count")
        ).filter(
            DeliveryReport.ship_to_city.isnot(None)
        ).group_by(
            DeliveryReport.ship_to_city
        ).order_by(
            func.count(DeliveryReport.id).desc()
        ).first()
        
        # Completed deliveries (PGI Completed + POD Received)
        completed_deliveries = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Received"
        ).count()
        
        # Delivered but not acknowledged (PGI Completed, POD Pending)
        delivered_not_acknowledged = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending"
        ).count()
        
        # Average delivery amount
        avg_amount = db.query(
            func.avg(DeliveryReport.dn_amount)
        ).scalar() or 0
        
        return {
            "success": True,
            "total_records": total_records,
            "pending_deliveries": pending_deliveries,
            "completed_deliveries": completed_deliveries,
            "delivered_not_acknowledged": delivered_not_acknowledged,
            "pending_pod": pending_pod,
            "pending_pgi": pending_pgi,
            "pending_amount": float(pending_amount),
            "top_warehouse": top_warehouse_row[0] if top_warehouse_row else "N/A",
            "top_city": top_city_row[0] if top_city_row else "N/A",
            "average_dn_amount": float(avg_amount)
        }
    
    # ======================================================
    # EXECUTIVE SUMMARY (ENHANCED)
    # ======================================================
    
    @staticmethod
    def get_executive_summary(db: Session) -> Dict[str, Any]:
        """Get executive-level dashboard summary."""
        
        insights = LogisticsQueryService.get_delivery_insights(db)
        top_dealers = LogisticsQueryService.get_top_dealers(db, 5)
        top_cities = LogisticsQueryService.get_top_cities(db, 5)
        highest_pending_dealer = LogisticsQueryService.get_highest_pending_dealer(db)
        highest_pending_warehouse = LogisticsQueryService.get_highest_pending_warehouse(db)
        
        completion_rate = round(
            (insights["completed_deliveries"] / insights["total_records"] * 100) 
            if insights["total_records"] > 0 else 0, 2
        )
        
        executive_summary = f"""Executive Logistics Report:

📊 OVERVIEW:
- Total Deliveries: {insights['total_records']}
- Completion Rate: {completion_rate}%
- Average Order Value: Rs {insights['average_dn_amount']:,.2f}

⏳ PENDING STATUS:
- Pending Deliveries: {insights['pending_deliveries']} (Rs {insights['pending_amount']:,.2f})
- Pending Dispatch (PGI): {insights['pending_pgi']}
- Awaiting Dealer Acknowledgement (POD): {insights['pending_pod']}
- Delivered but Not Acknowledged: {insights['delivered_not_acknowledged']}

🏆 TOP PERFORMERS:
- Top City: {insights['top_city']}
- Top Warehouse: {insights['top_warehouse']}
- Highest Pending Dealer: {highest_pending_dealer.get('dealer', 'N/A')} ({highest_pending_dealer.get('pending_count', 0)} pending)
- Highest Pending Warehouse: {highest_pending_warehouse.get('warehouse', 'N/A')} ({highest_pending_warehouse.get('pending_count', 0)} pending)

⚠️ RISKS & RECOMMENDATIONS:
- Focus on clearing pending dispatches from {highest_pending_warehouse.get('warehouse', 'warehouses')}
- Follow up with {highest_pending_dealer.get('dealer', 'dealers')} for pending acknowledgements
- Monitor {insights['top_city']} for delivery performance"""
        
        return {
            "success": True,
            "executive_summary": executive_summary,
            "total_records": insights["total_records"],
            "pending_deliveries": insights["pending_deliveries"],
            "pending_amount": insights["pending_amount"],
            "completion_rate": completion_rate,
            "top_city": insights["top_city"],
            "top_warehouse": insights["top_warehouse"],
            "average_dn_amount": insights["average_dn_amount"],
            "delivered_not_acknowledged": insights["delivered_not_acknowledged"],
            "top_dealers": top_dealers["records"],
            "top_cities": top_cities,
            "highest_pending_dealer": highest_pending_dealer,
            "highest_pending_warehouse": highest_pending_warehouse
        }
    
    # ======================================================
    # ORIGINAL METHODS (UPDATED TO RETURN DICTIONARIES)
    # ======================================================
    
    @staticmethod
    def get_dn_status(db: Session, dn_no: str):
        deliveries = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.dn_no == dn_no)
            .all()
        )
        
        if not deliveries:
            return {
                "success": False,
                "message": f"DN {dn_no} not found",
                "dn_no": dn_no
            }
        
        records = [LogisticsQueryService._record_to_dict(d) for d in deliveries]
        
        return {
            "success": True,
            "dn_no": dn_no,
            "total_lines": len(records),
            "records": records
        }
    
    @staticmethod
    def get_pending_deliveries(db: Session, limit: int = 100):
        rows = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.pending_flag.is_(True))
            .limit(limit)
            .all()
        )
        
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        total_amount = sum(r.get("dn_amount", 0) for r in records)
        
        return {
            "success": True,
            "count": len(records),
            "total_amount": float(total_amount),
            "records": records
        }
    
    @staticmethod
    def get_pending_pod(db: Session):
        count = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.pod_status == "Pending")
            .count()
        )
        
        return {
            "success": True,
            "pending_pod": count
        }
    
    @staticmethod
    def get_pending_pgi(db: Session):
        count = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.pgi_status == "Pending")
            .count()
        )
        
        return {
            "success": True,
            "pending_pgi": count
        }
    
    @staticmethod
    def get_city_deliveries(db: Session, city: str, limit: int = 100):
        rows = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.ship_to_city.ilike(f"%{city}%"))
            .limit(limit)
            .all()
        )
        
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        
        return {
            "success": True,
            "city": city,
            "count": len(records),
            "records": records
        }
    
    @staticmethod
    def get_dealer_deliveries(db: Session, dealer_code: str, limit: int = 100):
        rows = (
            db.query(DeliveryReport)
            .filter(
                or_(
                    DeliveryReport.dealer_code.ilike(f"%{dealer_code}%"),
                    DeliveryReport.customer_name.ilike(f"%{dealer_code}%")
                )
            )
            .limit(limit)
            .all()
        )
        
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        
        return {
            "success": True,
            "dealer_code": dealer_code,
            "count": len(records),
            "records": records
        }
    
    @staticmethod
    def get_warehouse_deliveries(db: Session, warehouse: str, limit: int = 100):
        rows = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.warehouse.ilike(f"%{warehouse}%"))
            .limit(limit)
            .all()
        )
        
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        
        return {
            "success": True,
            "warehouse": warehouse,
            "count": len(records),
            "records": records
        }
    
    @staticmethod
    def get_division_deliveries(db: Session, division: str):
        rows = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.division.ilike(f"%{division}%"))
            .all()
        )
        
        records = [LogisticsQueryService._record_to_dict(row) for row in rows]
        
        return {
            "success": True,
            "division": division,
            "count": len(records),
            "records": records
        }
    
    @staticmethod
    def get_dashboard_summary(db: Session):
        total_records = db.query(DeliveryReport).count()
        
        pending_deliveries = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).count()
        
        pending_pod = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending"
        ).count()
        
        pending_pgi = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Pending"
        ).count()
        
        pending_amount = db.query(
            func.sum(DeliveryReport.dn_amount)
        ).filter(
            DeliveryReport.pending_flag.is_(True)
        ).scalar() or 0
        
        return {
            "success": True,
            "total_records": total_records,
            "pending_deliveries": pending_deliveries,
            "pending_pod": pending_pod,
            "pending_pgi": pending_pgi,
            "pending_amount": float(pending_amount)
        }
    
    @staticmethod
    def get_top_dealers(db: Session, limit: int = 10) -> Dict[str, Any]:
        """Get top dealers by delivery count."""
        rows = (
            db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.id).label("delivery_count"),
                func.sum(DeliveryReport.dn_amount).label("total_amount")
            )
            .filter(DeliveryReport.customer_name.isnot(None))
            .group_by(DeliveryReport.customer_name)
            .order_by(func.count(DeliveryReport.id).desc())
            .limit(limit)
            .all()
        )
        
        records = [
            {
                "dealer_name": row.customer_name,
                "delivery_count": row.delivery_count,
                "total_amount": float(row.total_amount or 0)
            }
            for row in rows
        ]
        
        return {
            "success": True,
            "count": len(records),
            "records": records
        }
    
    @staticmethod
    def get_top_cities(db: Session, limit: int = 10):
        rows = (
            db.query(
                DeliveryReport.ship_to_city,
                func.count(DeliveryReport.id).label("count"),
                func.sum(DeliveryReport.dn_amount).label("total_amount")
            )
            .filter(DeliveryReport.ship_to_city.isnot(None))
            .group_by(DeliveryReport.ship_to_city)
            .order_by(func.count(DeliveryReport.id).desc())
            .limit(limit)
            .all()
        )
        
        return [
            {
                "city": row.ship_to_city,
                "count": row.count,
                "total_amount": float(row.total_amount or 0)
            }
            for row in rows
            if row.ship_to_city
        ]


# ======================================================
# CONVENIENCE FUNCTIONS FOR EXTERNAL USE
# ======================================================

def handle_logistics_query(question: str, db: Session) -> Dict[str, Any]:
    """Main entry point for WhatsApp queries (without AI)."""
    return LogisticsQueryService.generate_ai_context(question, db)


def handle_ai_query(question: str, db: Session, openai_client=None) -> Dict[str, Any]:
    """Main entry point for AI-powered WhatsApp queries."""
    return LogisticsQueryService.handle_ai_query(question, db, openai_client)


def get_ai_context(question: str, db: Session) -> Dict[str, Any]:
    """Get AI-ready context for GPT integration."""
    return LogisticsQueryService.generate_ai_context(question, db)


def build_gpt_prompt(question: str, context: Dict[str, Any]) -> str:
    """Build GPT prompt from question and context."""
    return LogisticsQueryService.build_gpt_prompt(question, context)
