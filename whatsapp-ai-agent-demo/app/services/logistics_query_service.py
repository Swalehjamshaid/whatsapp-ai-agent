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
    # PRIORITY 2: INTENT DETECTION
    # ======================================================
    
    @staticmethod
    def detect_intent(question: str) -> Dict[str, Any]:
        """
        Detect user intent from natural language question.
        
        Examples:
        - "6243612322" -> {"intent": "dn_lookup", "dn_no": "6243612322"}
        - "How many pending deliveries?" -> {"intent": "pending_deliveries"}
        - "Show Lahore deliveries" -> {"intent": "city_search", "city": "Lahore"}
        """
        question_lower = question.lower().strip()
        
        # Check for DN number (numeric string of 8-15 digits)
        dn_match = re.search(r'\b(\d{8,15})\b', question)
        if dn_match:
            return {
                "intent": "dn_lookup",
                "dn_no": dn_match.group(1)
            }
        
        # Pending deliveries intent
        pending_keywords = ["pending delivery", "pending deliveries", "how many pending", "pending orders", "undelivered"]
        if any(keyword in question_lower for keyword in pending_keywords):
            return {"intent": "pending_deliveries"}
        
        # Pending POD intent
        pod_keywords = ["pending pod", "pod pending", "delivery proof pending", "signature pending"]
        if any(keyword in question_lower for keyword in pod_keywords):
            return {"intent": "pending_pod"}
        
        # Pending PGI intent
        pgi_keywords = ["pending pgi", "pgi pending", "goods issue pending", "not dispatched"]
        if any(keyword in question_lower for keyword in pgi_keywords):
            return {"intent": "pending_pgi"}
        
        # City search
        city_patterns = [
            r'in\s+(\w+)', r'for\s+(\w+)', r'at\s+(\w+)',
            r'(\w+)\s+deliveries', r'(\w+)\s+customers'
        ]
        for pattern in city_patterns:
            city_match = re.search(pattern, question_lower)
            if city_match:
                city = city_match.group(1).title()
                return {"intent": "city_search", "city": city}
        
        # Dealer/customer search
        dealer_keywords = ["dealer", "customer", "show me deliveries for"]
        for keyword in dealer_keywords:
            if keyword in question_lower:
                # Extract dealer name after keyword
                parts = question_lower.split(keyword)
                if len(parts) > 1:
                    dealer_name = parts[1].strip().title()
                    if dealer_name:
                        return {"intent": "dealer_search", "dealer_name": dealer_name}
        
        # Material search
        material_match = re.search(r'material\s+(\d+|\w+)', question_lower)
        if material_match:
            return {
                "intent": "material_search",
                "material_no": material_match.group(1)
            }
        
        # Warehouse search
        warehouse_keywords = ["warehouse", "stock from", "from warehouse"]
        for keyword in warehouse_keywords:
            if keyword in question_lower:
                parts = question_lower.split(keyword)
                if len(parts) > 1:
                    warehouse = parts[1].strip().upper()
                    if warehouse:
                        return {"intent": "warehouse_search", "warehouse": warehouse}
        
        # Division search
        division_match = re.search(r'division\s+(\w+)', question_lower)
        if division_match:
            return {
                "intent": "division_search",
                "division": division_match.group(1).upper()
            }
        
        # Summary/insights intent
        summary_keywords = ["summary", "dashboard", "overview", "report", "statistics", "insights", "analytics"]
        if any(keyword in question_lower for keyword in summary_keywords):
            return {"intent": "dashboard_summary"}
        
        # Top dealers/cities intent
        if "top" in question_lower:
            if "dealer" in question_lower or "customer" in question_lower:
                return {"intent": "top_dealers"}
            if "city" in question_lower:
                return {"intent": "top_cities"}
        
        # Completed deliveries
        if "completed" in question_lower and ("delivery" in question_lower or "deliveries" in question_lower):
            return {"intent": "completed_deliveries"}
        
        # Default fallback
        return {"intent": "general_query"}
    
    # ======================================================
    # PRIORITY 1: AI CONTEXT BUILDER
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
            if result["success"]:
                result["summary"] = LogisticsQueryService.generate_dn_summary(result)
            return result
        
        elif intent == "pending_deliveries":
            result = LogisticsQueryService.get_pending_deliveries(db)
            result["summary"] = LogisticsQueryService.generate_pending_summary(result)
            return result
        
        elif intent == "pending_pod":
            result = LogisticsQueryService.get_pending_pod(db)
            result["summary"] = f"There are {result['pending_pod']} deliveries waiting for Proof of Delivery (POD)."
            return result
        
        elif intent == "pending_pgi":
            result = LogisticsQueryService.get_pending_pgi(db)
            result["summary"] = f"There are {result['pending_pgi']} deliveries waiting for Goods Issue (PGI)."
            return result
        
        elif intent == "city_search":
            result = LogisticsQueryService.get_city_deliveries(db, intent_result["city"])
            result["summary"] = LogisticsQueryService.generate_city_summary(result)
            return result
        
        elif intent == "dealer_search":
            result = LogisticsQueryService.get_dealer_deliveries(db, intent_result["dealer_name"])
            result["summary"] = LogisticsQueryService.generate_dealer_summary(result)
            return result
        
        elif intent == "material_search":
            result = LogisticsQueryService.search_material(db, intent_result["material_no"])
            result["summary"] = LogisticsQueryService.generate_material_summary(result)
            return result
        
        elif intent == "warehouse_search":
            result = LogisticsQueryService.get_warehouse_deliveries(db, intent_result["warehouse"])
            result["summary"] = LogisticsQueryService.generate_warehouse_summary(result)
            return result
        
        elif intent == "division_search":
            result = LogisticsQueryService.get_division_deliveries(db, intent_result["division"])
            result["summary"] = LogisticsQueryService.generate_division_summary(result)
            return result
        
        elif intent == "dashboard_summary":
            result = LogisticsQueryService.get_delivery_insights(db)
            result["summary"] = LogisticsQueryService.generate_insights_summary(result)
            return result
        
        elif intent == "top_dealers":
            result = LogisticsQueryService.get_top_dealers(db)
            result["summary"] = f"Top dealers: {', '.join([d['dealer_name'] for d in result['records'][:5]])}"
            return result
        
        elif intent == "top_cities":
            result = LogisticsQueryService.get_top_cities(db)
            result["summary"] = f"Top cities by delivery volume: {', '.join([c['city'] for c in result[:5]])}"
            return result
        
        elif intent == "completed_deliveries":
            result = LogisticsQueryService.get_completed_deliveries(db)
            result["summary"] = f"There are {result['count']} completed deliveries."
            return result
        
        else:
            # Return general dashboard for unknown queries
            result = LogisticsQueryService.get_delivery_insights(db)
            result["summary"] = "Here's the current logistics dashboard summary."
            return result
    
    # ======================================================
    # PRIORITY 3: AI SUMMARY FOR DN SEARCH
    # ======================================================
    
    @staticmethod
    def generate_dn_summary(dn_result: Dict[str, Any]) -> str:
        """Generate natural language summary for DN lookup."""
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
        status = main.get("delivery_status", "Unknown")
        pgi_status = main.get("pgi_status", "Unknown")
        pod_status = main.get("pod_status", "Unknown")
        amount = main.get("dn_amount", 0)
        
        summary = f"DN {dn_result['dn_no']} belongs to {customer} in {city}. "
        
        if status == "Completed":
            summary += f"This delivery is complete with POD received. "
        elif status == "In Transit":
            summary += f"The shipment is in transit. PGI is completed but POD is pending. "
        else:
            summary += f"This delivery is pending. "
        
        summary += f"Warehouse: {warehouse}. Amount: Rs {amount:,.2f}. "
        summary += f"PGI Status: {pgi_status}. POD Status: {pod_status}."
        
        if len(records) > 1:
            summary += f" There are {len(records)} line items in this delivery."
        
        return summary
    
    # ======================================================
    # ADDITIONAL AI SUMMARIES
    # ======================================================
    
    @staticmethod
    def generate_pending_summary(result: Dict[str, Any]) -> str:
        """Generate summary for pending deliveries."""
        count = result.get("count", 0)
        if count == 0:
            return "There are no pending deliveries. All deliveries are complete!"
        
        # Calculate pending amount if available
        total_amount = sum(r.dn_amount or 0 for r in result.get("records", []))
        
        return f"There are {count} pending deliveries totaling Rs {total_amount:,.2f}. These deliveries have not been completed yet."
    
    @staticmethod
    def generate_city_summary(result: Dict[str, Any]) -> str:
        """Generate summary for city search."""
        city = result.get("city", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for {city}."
        
        # Count statuses
        records = result.get("records", [])
        completed = sum(1 for r in records if r.delivery_status == "Completed")
        pending = sum(1 for r in records if r.pending_flag)
        
        return f"Found {count} deliveries for {city}. {completed} completed, {pending} pending."
    
    @staticmethod
    def generate_dealer_summary(result: Dict[str, Any]) -> str:
        """Generate summary for dealer search."""
        dealer = result.get("dealer_code", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for dealer {dealer}."
        
        records = result.get("records", [])
        total_amount = sum(r.dn_amount or 0 for r in records)
        
        return f"Dealer {dealer} has {count} deliveries totaling Rs {total_amount:,.2f}."
    
    @staticmethod
    def generate_material_summary(result: Dict[str, Any]) -> str:
        """Generate summary for material search."""
        material = result.get("material_no", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for material {material}."
        
        total_qty = sum(r.dn_qty or 0 for r in result.get("records", []))
        
        return f"Material {material} appears in {count} deliveries with total quantity {total_qty}."
    
    @staticmethod
    def generate_warehouse_summary(result: Dict[str, Any]) -> str:
        """Generate summary for warehouse search."""
        warehouse = result.get("warehouse", "Unknown")
        count = result.get("count", 0)
        
        if count == 0:
            return f"No deliveries found for warehouse {warehouse}."
        
        records = result.get("records", [])
        pending = sum(1 for r in records if r.pending_flag)
        
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
        """Generate summary for delivery insights."""
        return (
            f"Logistics Summary: {result['total_records']} total deliveries. "
            f"{result['pending_deliveries']} pending (Rs {result['pending_amount']:,.2f}). "
            f"{result['pending_pgi']} pending PGI, {result['pending_pod']} pending POD. "
            f"Top city: {result['top_city']}. Top warehouse: {result['top_warehouse']}."
        )
    
    # ======================================================
    # PRIORITY 4: DELIVERY ANALYTICS
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
        top_warehouse = db.query(
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
        top_city = db.query(
            DeliveryReport.ship_to_city,
            func.count(DeliveryReport.id).label("count")
        ).filter(
            DeliveryReport.ship_to_city.isnot(None)
        ).group_by(
            DeliveryReport.ship_to_city
        ).order_by(
            func.count(DeliveryReport.id).desc()
        ).first()
        
        # Completed deliveries
        completed_deliveries = db.query(DeliveryReport).filter(
            DeliveryReport.delivery_status == "Completed"
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
            "pending_pod": pending_pod,
            "pending_pgi": pending_pgi,
            "pending_amount": float(pending_amount),
            "top_warehouse": top_warehouse[0] if top_warehouse else "N/A",
            "top_city": top_city[0] if top_city else "N/A",
            "average_dn_amount": float(avg_amount)
        }
    
    # ======================================================
    # PRIORITY 5: TOP DEALERS FUNCTION
    # ======================================================
    
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
    
    # ======================================================
    # PRIORITY 6: SEARCH BY CUSTOMER
    # ======================================================
    
    @staticmethod
    def search_customer(db: Session, customer_name: str, limit: int = 50) -> Dict[str, Any]:
        """Search deliveries by customer name."""
        rows = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.customer_name.ilike(f"%{customer_name}%")
            )
            .limit(limit)
            .all()
        )
        
        return {
            "success": True,
            "customer_name": customer_name,
            "count": len(rows),
            "records": rows
        }
    
    # ======================================================
    # PRIORITY 7: SEARCH BY MATERIAL NUMBER
    # ======================================================
    
    @staticmethod
    def search_material(db: Session, material_no: str, limit: int = 50) -> Dict[str, Any]:
        """Search deliveries by material number."""
        rows = (
            db.query(DeliveryReport)
            .filter(
                DeliveryReport.material_no.ilike(f"%{material_no}%")
            )
            .limit(limit)
            .all()
        )
        
        total_qty = sum(r.dn_qty or 0 for r in rows)
        
        return {
            "success": True,
            "material_no": material_no,
            "count": len(rows),
            "total_quantity": float(total_qty),
            "records": rows
        }
    
    # ======================================================
    # PRIORITY 8: COMPLETED DELIVERIES
    # ======================================================
    
    @staticmethod
    def get_completed_deliveries(db: Session, limit: int = 100) -> Dict[str, Any]:
        """Get completed deliveries."""
        rows = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.delivery_status == "Completed")
            .limit(limit)
            .all()
        )
        
        total_amount = sum(r.dn_amount or 0 for r in rows)
        
        return {
            "success": True,
            "count": len(rows),
            "total_amount": float(total_amount),
            "records": rows
        }
    
    # ======================================================
    # PRIORITY 9: EXECUTIVE SUMMARY
    # ======================================================
    
    @staticmethod
    def get_executive_summary(db: Session) -> Dict[str, Any]:
        """Get executive-level dashboard summary."""
        
        insights = LogisticsQueryService.get_delivery_insights(db)
        top_dealers = LogisticsQueryService.get_top_dealers(db, 5)
        top_cities = LogisticsQueryService.get_top_cities(db, 5)
        
        return {
            "success": True,
            "summary": {
                "total_records": insights["total_records"],
                "pending_deliveries": insights["pending_deliveries"],
                "pending_amount": insights["pending_amount"],
                "completion_rate": round(
                    (insights["completed_deliveries"] / insights["total_records"] * 100) 
                    if insights["total_records"] > 0 else 0, 2
                ),
                "top_city": insights["top_city"],
                "top_warehouse": insights["top_warehouse"],
                "average_dn_amount": insights["average_dn_amount"]
            },
            "top_dealers": top_dealers["records"],
            "top_cities": top_cities
        }
    
    # ======================================================
    # PRIORITY 10: UNIFIED QUERY HANDLER
    # ======================================================
    
    @staticmethod
    def handle_query(question: str, db: Session) -> Dict[str, Any]:
        """
        Unified entry point for WhatsApp queries.
        This automatically detects intent and returns AI-ready responses.
        """
        # Generate AI context (this handles intent detection and routing)
        result = LogisticsQueryService.generate_ai_context(question, db)
        
        # Ensure all responses have required fields for GPT
        if "success" not in result:
            result["success"] = True
        
        if "summary" not in result:
            result["summary"] = "Query processed successfully."
        
        return result
    
    # ======================================================
    # ORIGINAL METHODS (KEPT FOR BACKWARD COMPATIBILITY)
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
        
        return {
            "success": True,
            "dn_no": dn_no,
            "total_lines": len(deliveries),
            "records": [
                {
                    "dealer_code": d.dealer_code,
                    "customer_name": d.customer_name,
                    "material_no": d.material_no,
                    "city": d.ship_to_city,
                    "warehouse": d.warehouse,
                    "division": d.division,
                    "delivery_status": d.delivery_status,
                    "pgi_status": d.pgi_status,
                    "pod_status": d.pod_status,
                    "pending_flag": d.pending_flag,
                    "dn_amount": float(d.dn_amount or 0),
                    "dn_qty": float(d.dn_qty or 0)
                }
                for d in deliveries
            ]
        }
    
    @staticmethod
    def get_pending_deliveries(db: Session, limit: int = 100):
        rows = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.pending_flag.is_(True))
            .limit(limit)
            .all()
        )
        
        total_amount = sum(r.dn_amount or 0 for r in rows)
        
        return {
            "success": True,
            "count": len(rows),
            "total_amount": float(total_amount),
            "records": rows
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
        
        return {
            "success": True,
            "city": city,
            "count": len(rows),
            "records": rows
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
        
        return {
            "success": True,
            "dealer_code": dealer_code,
            "count": len(rows),
            "records": rows
        }
    
    @staticmethod
    def get_warehouse_deliveries(db: Session, warehouse: str, limit: int = 100):
        rows = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.warehouse.ilike(f"%{warehouse}%"))
            .limit(limit)
            .all()
        )
        
        return {
            "success": True,
            "warehouse": warehouse,
            "count": len(rows),
            "records": rows
        }
    
    @staticmethod
    def get_division_deliveries(db: Session, division: str):
        rows = (
            db.query(DeliveryReport)
            .filter(DeliveryReport.division.ilike(f"%{division}%"))
            .all()
        )
        
        return {
            "success": True,
            "division": division,
            "count": len(rows),
            "records": rows
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
    """Main entry point for WhatsApp queries."""
    return LogisticsQueryService.handle_query(question, db)


def get_ai_context(question: str, db: Session) -> Dict[str, Any]:
    """Get AI-ready context for GPT integration."""
    return LogisticsQueryService.generate_ai_context(question, db)
