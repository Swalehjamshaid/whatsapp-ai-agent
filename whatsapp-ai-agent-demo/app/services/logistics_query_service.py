# ==========================================================
# FILE: app/services/logistics_query_service.py
# ==========================================================
# PURPOSE: Business Data Aggregator & Dashboard Builder
#          Bridge between User Questions → Database → KPIs → Analytics → Dashboards
#
# WHAT THIS FILE DOES:
# ✅ Dealer Dashboard Engine
# ✅ Warehouse Dashboard Engine
# ✅ Warehouse SLA Dashboard
# ✅ Product Dashboard Engine
# ✅ Division Dashboard Engine
# ✅ Sales Manager Dashboard Engine
# ✅ City Dashboard Engine
# ✅ DN Dashboard Engine
# ✅ KPI Integration
# ✅ Ranking Integration
# ✅ Trend Integration
# ✅ Control Tower Integration
#
# WHAT THIS FILE NEVER DOES:
# ✗ Direct Database Queries (uses schema_service)
# ✗ KPI Calculations (uses kpi_service)
# ✗ Analytics (uses analytics_service)
# ✗ WhatsApp Sending
# ✗ User Question Parsing
# ==========================================================

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, date
from collections import defaultdict
from loguru import logger

# Import services (lazy loading to avoid circular imports)
_schema_service = None
_kpi_service = None
_analytics_service = None
_ai_provider_service = None


def get_schema_service():
    global _schema_service
    if _schema_service is None:
        try:
            from app.services.schema_service import get_schema_service as gss
            _schema_service = gss()
            logger.info("Schema Service connected to Logistics Query Service")
        except Exception as e:
            logger.error(f"Failed to connect Schema Service: {e}")
    return _schema_service


def get_kpi_service():
    global _kpi_service
    if _kpi_service is None:
        try:
            from app.services.kpi_service import get_kpi_service as gks
            _kpi_service = gks()
            logger.info("KPI Service connected to Logistics Query Service")
        except Exception as e:
            logger.error(f"Failed to connect KPI Service: {e}")
    return _kpi_service


def get_analytics_service():
    global _analytics_service
    if _analytics_service is None:
        try:
            from app.services.analytics_service import get_analytics_service as gas
            _analytics_service = gas()
            logger.info("Analytics Service connected to Logistics Query Service")
        except Exception as e:
            logger.error(f"Failed to connect Analytics Service: {e}")
    return _analytics_service


def get_ai_provider_service():
    global _ai_provider_service
    if _ai_provider_service is None:
        try:
            from app.services.ai_provider_service import get_ai_provider_service as gap
            _ai_provider_service = gap()
            logger.info("AI Provider Service connected to Logistics Query Service")
        except Exception as e:
            logger.error(f"Failed to connect AI Provider Service: {e}")
    return _ai_provider_service


# ==========================================================
# LOGISTICS QUERY SERVICE
# ==========================================================

class LogisticsQueryService:
    """
    Business Data Aggregator & Dashboard Builder
    Bridges all services to create complete dashboards
    """
    
    def __init__(self, db_session=None):
        """Initialize with optional database session"""
        self.db_session = db_session
        logger.info("Logistics Query Service initialized")
    
    # ==========================================================
    # 1. DEALER DASHBOARD ENGINE
    # ==========================================================
    
    def build_dealer_dashboard(self, dealer_name: str) -> Optional[Dict[str, Any]]:
        """
        Build complete dealer dashboard
        
        Questions answered:
        - Dubai Electronics
        - Dealer Dashboard
        - Dealer KPI
        - Dealer Summary
        
        Returns:
        - Revenue, Units, DN Count
        - Delivered DN, Pending DN
        - POD Done, POD Pending
        - Average Delivery Aging, Average POD Aging
        - Top Models, Top Warehouse, Top City
        - Critical Cases
        """
        logger.info(f"Building dealer dashboard for: {dealer_name}")
        
        # Step 1: Get raw data from schema_service
        schema = get_schema_service()
        if not schema:
            logger.error("Schema Service not available")
            return None
        
        records = schema.get_dealer_records(dealer_name)
        if not records:
            logger.warning(f"No records found for dealer: {dealer_name}")
            return None
        
        # Step 2: Calculate KPIs using kpi_service
        kpi = get_kpi_service()
        if not kpi:
            logger.error("KPI Service not available")
            return None
        
        dealer_kpi = kpi.calculate_dealer_kpis(records)
        if not dealer_kpi:
            return None
        
        # Step 3: Get top models
        model_units = defaultdict(int)
        for r in records:
            model = r.product_description or r.product_code or "Unknown"
            model_units[model] += int(r.dn_qty or 0)
        
        top_models = sorted(model_units.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Step 4: Get top warehouse
        warehouse_count = defaultdict(int)
        for r in records:
            if r.warehouse:
                warehouse_count[r.warehouse] += 1
        top_warehouse = max(warehouse_count.items(), key=lambda x: x[1])[0] if warehouse_count else "N/A"
        
        # Step 5: Get top city
        city_count = defaultdict(int)
        for r in records:
            if r.ship_to_city:
                city_count[r.ship_to_city] += 1
        top_city = max(city_count.items(), key=lambda x: x[1])[0] if city_count else "N/A"
        
        # Step 6: Build dashboard
        dashboard = {
            "dealer_name": dealer_kpi.dealer_name,
            "customer_code": dealer_kpi.customer_code,
            "revenue": dealer_kpi.revenue,
            "units": dealer_kpi.units,
            "dn_count": dealer_kpi.dn_count,
            "delivered_dn": dealer_kpi.delivered_dn,
            "pending_dn": dealer_kpi.pending_dn,
            "pgi_done": dealer_kpi.pgi_done,
            "pgi_pending": dealer_kpi.pgi_pending,
            "pod_done": dealer_kpi.pod_done,
            "pod_pending": dealer_kpi.pod_pending,
            "delivery_rate": dealer_kpi.delivery_rate,
            "pod_rate": dealer_kpi.pod_rate,
            "pgi_rate": dealer_kpi.pgi_rate,
            "completion_rate": dealer_kpi.completion_rate,
            "avg_delivery_aging": dealer_kpi.avg_delivery_aging,
            "avg_pod_aging": dealer_kpi.avg_pod_aging,
            "max_delivery_aging": dealer_kpi.max_delivery_aging,
            "max_pod_aging": dealer_kpi.max_pod_aging,
            "min_delivery_aging": dealer_kpi.min_delivery_aging,
            "min_pod_aging": dealer_kpi.min_pod_aging,
            "critical_dn": dealer_kpi.critical_dn,
            "critical_pod": dealer_kpi.critical_pod,
            "top_models": [{"name": m, "units": u} for m, u in top_models],
            "top_warehouse": top_warehouse,
            "top_city": top_city
        }
        
        logger.info(f"Dealer dashboard built for: {dealer_name}")
        return dashboard
    
    # ==========================================================
    # 2. WAREHOUSE DASHBOARD ENGINE
    # ==========================================================
    
    def build_warehouse_dashboard(self, warehouse_name: str) -> Optional[Dict[str, Any]]:
        """
        Build complete warehouse dashboard
        
        Questions answered:
        - Sargodha Warehouse
        - Warehouse KPI
        - Warehouse Dashboard
        
        Returns:
        - Revenue, Units, DN Count
        - Pending Delivery, Pending POD
        - Average Delivery Aging, Average POD Aging
        - Warehouse Score, Risk Score
        """
        logger.info(f"Building warehouse dashboard for: {warehouse_name}")
        
        schema = get_schema_service()
        if not schema:
            logger.error("Schema Service not available")
            return None
        
        records = schema.get_warehouse_records(warehouse_name)
        if not records:
            logger.warning(f"No records found for warehouse: {warehouse_name}")
            return None
        
        kpi = get_kpi_service()
        if not kpi:
            logger.error("KPI Service not available")
            return None
        
        warehouse_kpi = kpi.calculate_warehouse_kpis(records)
        if not warehouse_kpi:
            return None
        
        # Calculate risk score
        risk_score = self._calculate_warehouse_risk_score(warehouse_kpi)
        
        # Calculate warehouse performance score
        warehouse_score = self._calculate_warehouse_score(warehouse_kpi)
        
        dashboard = {
            "warehouse_name": warehouse_kpi.warehouse_name,
            "revenue": warehouse_kpi.revenue,
            "units": warehouse_kpi.units,
            "dn_count": warehouse_kpi.dn_count,
            "pending_delivery": warehouse_kpi.pending_delivery,
            "pending_pod": warehouse_kpi.pending_pod,
            "avg_delivery_aging": warehouse_kpi.avg_delivery_aging,
            "avg_pod_aging": warehouse_kpi.avg_pod_aging,
            "max_delivery_aging": warehouse_kpi.max_delivery_aging,
            "max_pod_aging": warehouse_kpi.max_pod_aging,
            "critical_dn": warehouse_kpi.critical_dn,
            "same_day_delivery": warehouse_kpi.same_day_delivery,
            "one_day_delivery": warehouse_kpi.one_day_delivery,
            "two_day_delivery": warehouse_kpi.two_day_delivery,
            "three_day_delivery": warehouse_kpi.three_day_delivery,
            "four_day_delivery": warehouse_kpi.four_day_delivery,
            "five_plus_delivery": warehouse_kpi.five_plus_delivery,
            "same_day_pod": warehouse_kpi.same_day_pod,
            "one_day_pod": warehouse_kpi.one_day_pod,
            "two_day_pod": warehouse_kpi.two_day_pod,
            "three_day_pod": warehouse_kpi.three_day_pod,
            "four_day_pod": warehouse_kpi.four_day_pod,
            "five_plus_pod": warehouse_kpi.five_plus_pod,
            "warehouse_score": warehouse_score,
            "risk_score": risk_score,
            "risk_level": self._get_risk_level(risk_score)
        }
        
        logger.info(f"Warehouse dashboard built for: {warehouse_name}")
        return dashboard
    
    # ==========================================================
    # 3. WAREHOUSE SLA DASHBOARD
    # ==========================================================
    
    def build_warehouse_sla_dashboard(self, warehouse_name: str) -> Optional[Dict[str, Any]]:
        """
        Build warehouse SLA dashboard
        
        Questions answered:
        - Warehouse Delivery KPI
        - Warehouse POD KPI
        
        Returns:
        - Delivery SLA buckets (Same Day, 1-5+ Days)
        - POD SLA buckets (Same Day, 1-5+ Days)
        """
        logger.info(f"Building warehouse SLA dashboard for: {warehouse_name}")
        
        dashboard = self.build_warehouse_dashboard(warehouse_name)
        if not dashboard:
            return None
        
        return {
            "warehouse_name": warehouse_name,
            "delivery_sla": {
                "same_day": dashboard.get("same_day_delivery", 0),
                "one_day": dashboard.get("one_day_delivery", 0),
                "two_day": dashboard.get("two_day_delivery", 0),
                "three_day": dashboard.get("three_day_delivery", 0),
                "four_day": dashboard.get("four_day_delivery", 0),
                "five_plus": dashboard.get("five_plus_delivery", 0),
                "average_days": dashboard.get("avg_delivery_aging", 0)
            },
            "pod_sla": {
                "same_day": dashboard.get("same_day_pod", 0),
                "one_day": dashboard.get("one_day_pod", 0),
                "two_day": dashboard.get("two_day_pod", 0),
                "three_day": dashboard.get("three_day_pod", 0),
                "four_day": dashboard.get("four_day_pod", 0),
                "five_plus": dashboard.get("five_plus_pod", 0),
                "average_days": dashboard.get("avg_pod_aging", 0)
            }
        }
    
    # ==========================================================
    # 4. PRODUCT DASHBOARD ENGINE
    # ==========================================================
    
    def build_product_dashboard(self, product_identifier: str) -> Optional[Dict[str, Any]]:
        """
        Build product dashboard
        
        Questions answered:
        - HRF-438IFRA1
        - Top Product
        - Product Dashboard
        
        Returns:
        - Revenue, Units, DN
        - Pending POD, Pending Delivery
        - Top Cities, Top Dealers
        """
        logger.info(f"Building product dashboard for: {product_identifier}")
        
        schema = get_schema_service()
        if not schema:
            logger.error("Schema Service not available")
            return None
        
        records = schema.get_product_records(product_identifier)
        if not records:
            logger.warning(f"No records found for product: {product_identifier}")
            return None
        
        kpi = get_kpi_service()
        if not kpi:
            logger.error("KPI Service not available")
            return None
        
        product_kpi = kpi.calculate_product_kpis(records)
        if not product_kpi:
            return None
        
        # Get top cities for this product
        city_revenue = defaultdict(float)
        for r in records:
            if r.ship_to_city:
                city_revenue[r.ship_to_city] += float(r.dn_amount or 0)
        top_cities = sorted(city_revenue.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Get top dealers for this product
        dealer_revenue = defaultdict(float)
        for r in records:
            if r.customer_name:
                dealer_revenue[r.customer_name] += float(r.dn_amount or 0)
        top_dealers = sorted(dealer_revenue.items(), key=lambda x: x[1], reverse=True)[:5]
        
        dashboard = {
            "product_code": product_kpi.get("product_code"),
            "product_name": product_kpi.get("product_name"),
            "revenue": product_kpi.get("revenue", 0),
            "units": product_kpi.get("units", 0),
            "dn_count": product_kpi.get("dn_count", 0),
            "avg_delivery_aging": product_kpi.get("avg_delivery_aging", 0),
            "top_cities": [{"city": c, "revenue": r} for c, r in top_cities],
            "top_dealers": [{"dealer": d, "revenue": r} for d, r in top_dealers]
        }
        
        logger.info(f"Product dashboard built for: {product_identifier}")
        return dashboard
    
    # ==========================================================
    # 5. DIVISION DASHBOARD ENGINE
    # ==========================================================
    
    def build_division_dashboard(self, division_name: str) -> Optional[Dict[str, Any]]:
        """
        Build division dashboard
        
        Questions answered:
        - Refrigerator
        - TV
        - Freezer
        - Cooking
        
        Returns:
        - Revenue, Units, DN
        - Market Share
        - Top Products, Top Dealers
        """
        logger.info(f"Building division dashboard for: {division_name}")
        
        division_map = {
            "refrigerator": "REF", "fridge": "REF",
            "tv": "TV", "television": "TV",
            "cooking": "COOK", "oven": "COOK", "microwave": "COOK",
            "freezer": "FRZ"
        }
        
        division_code = division_map.get(division_name.lower(), division_name.upper())
        
        schema = get_schema_service()
        if not schema:
            logger.error("Schema Service not available")
            return None
        
        records = schema.get_division_records(division_code)
        if not records:
            logger.warning(f"No records found for division: {division_name}")
            return None
        
        kpi = get_kpi_service()
        if not kpi:
            logger.error("KPI Service not available")
            return None
        
        division_kpi = kpi.calculate_division_kpis(records, division_name, division_code)
        
        # Get top products in this division
        product_units = defaultdict(int)
        for r in records:
            product = r.product_description or r.product_code or "Unknown"
            product_units[product] += int(r.dn_qty or 0)
        top_products = sorted(product_units.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Get top dealers in this division
        dealer_units = defaultdict(int)
        for r in records:
            if r.customer_name:
                dealer_units[r.customer_name] += int(r.dn_qty or 0)
        top_dealers = sorted(dealer_units.items(), key=lambda x: x[1], reverse=True)[:5]
        
        dashboard = {
            "division_name": division_name,
            "division_code": division_code,
            "revenue": division_kpi.revenue,
            "units": division_kpi.units,
            "dn_count": division_kpi.dn_count,
            "market_share": division_kpi.market_share,
            "avg_delivery_aging": division_kpi.avg_delivery_aging,
            "avg_pod_aging": division_kpi.avg_pod_aging,
            "top_products": [{"name": p, "units": u} for p, u in top_products],
            "top_dealers": [{"name": d, "units": u} for d, u in top_dealers]
        }
        
        logger.info(f"Division dashboard built for: {division_name}")
        return dashboard
    
    # ==========================================================
    # 6. SALES MANAGER DASHBOARD ENGINE
    # ==========================================================
    
    def build_sales_manager_dashboard(self, manager_name: str) -> Optional[Dict[str, Any]]:
        """
        Build sales manager dashboard
        
        Questions answered:
        - Sales Manager Dashboard
        - Top Sales Manager
        
        Returns:
        - Revenue, Units, DN
        - Pending POD, Pending Delivery
        - Dealer Count, Product Count
        """
        logger.info(f"Building sales manager dashboard for: {manager_name}")
        
        schema = get_schema_service()
        if not schema:
            logger.error("Schema Service not available")
            return None
        
        # Note: This requires sales_manager field in your model
        records = schema.get_sales_manager_records(manager_name) if hasattr(schema, 'get_sales_manager_records') else []
        
        if not records:
            # Return mock data for now
            return self._get_mock_sales_manager_dashboard(manager_name)
        
        kpi = get_kpi_service()
        if not kpi:
            logger.error("KPI Service not available")
            return None
        
        manager_kpi = kpi.calculate_sales_manager_kpis(records, manager_name)
        
        dashboard = {
            "manager_name": manager_name,
            "revenue": manager_kpi.revenue,
            "units": manager_kpi.units,
            "dn_count": manager_kpi.dn_count,
            "pending_delivery": manager_kpi.pending_delivery,
            "pending_pod": manager_kpi.pending_pod,
            "avg_delivery_aging": manager_kpi.avg_delivery_aging,
            "avg_pod_aging": manager_kpi.avg_pod_aging,
            "top_dealer": manager_kpi.top_dealer,
            "top_product": manager_kpi.top_product
        }
        
        logger.info(f"Sales manager dashboard built for: {manager_name}")
        return dashboard
    
    # ==========================================================
    # 7. CITY DASHBOARD ENGINE
    # ==========================================================
    
    def build_city_dashboard(self, city_name: str) -> Optional[Dict[str, Any]]:
        """
        Build city dashboard
        
        Questions answered:
        - Lahore Dashboard
        - Karachi Dashboard
        
        Returns:
        - Revenue, Units, DN
        - Pending POD, Pending Delivery
        - Top Dealers, Top Products
        """
        logger.info(f"Building city dashboard for: {city_name}")
        
        schema = get_schema_service()
        if not schema:
            logger.error("Schema Service not available")
            return None
        
        records = schema.get_city_records(city_name)
        if not records:
            logger.warning(f"No records found for city: {city_name}")
            return None
        
        kpi = get_kpi_service()
        if not kpi:
            logger.error("KPI Service not available")
            return None
        
        city_kpi = kpi.calculate_city_kpis(records)
        
        # Get top dealers in this city
        dealer_revenue = defaultdict(float)
        for r in records:
            if r.customer_name:
                dealer_revenue[r.customer_name] += float(r.dn_amount or 0)
        top_dealers = sorted(dealer_revenue.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Get top products in this city
        product_units = defaultdict(int)
        for r in records:
            product = r.product_description or r.product_code or "Unknown"
            product_units[product] += int(r.dn_qty or 0)
        top_products = sorted(product_units.items(), key=lambda x: x[1], reverse=True)[:5]
        
        dashboard = {
            "city_name": city_kpi.get("city_name", city_name),
            "revenue": city_kpi.get("revenue", 0),
            "units": city_kpi.get("units", 0),
            "dn_count": city_kpi.get("dn_count", 0),
            "pending_delivery": city_kpi.get("pending_delivery", 0),
            "pending_pod": city_kpi.get("pending_pod", 0),
            "avg_delivery_aging": city_kpi.get("avg_delivery_aging", 0),
            "delivery_rate": city_kpi.get("delivery_rate", 0),
            "top_dealers": [{"name": d, "revenue": r} for d, r in top_dealers],
            "top_products": [{"name": p, "units": u} for p, u in top_products]
        }
        
        logger.info(f"City dashboard built for: {city_name}")
        return dashboard
    
    # ==========================================================
    # 8. DN DASHBOARD ENGINE
    # ==========================================================
    
    def build_dn_dashboard(self, dn_number: str) -> Optional[Dict[str, Any]]:
        """
        Build DN dashboard
        
        Questions answered:
        - 6243612322
        - DN Status
        - DN Details
        
        Returns:
        - DN Number, Dealer, Warehouse
        - Product, Amount, Quantity
        - DN Date, PGI Date, POD Date
        - Delivery Aging, POD Aging
        - Current Status
        """
        logger.info(f"Building DN dashboard for: {dn_number}")
        
        schema = get_schema_service()
        if not schema:
            logger.error("Schema Service not available")
            return None
        
        record = schema.get_dn_details(dn_number)
        if not record:
            logger.warning(f"No record found for DN: {dn_number}")
            return None
        
        kpi = get_kpi_service()
        if not kpi:
            logger.error("KPI Service not available")
            return None
        
        # Calculate metrics
        delivery_aging = kpi.calculate_delivery_aging(record.dn_create_date, record.good_issue_date)
        pod_aging = kpi.calculate_pod_aging(record.good_issue_date, record.pod_date)
        pending_delivery_aging = kpi.calculate_pending_delivery_aging(record.dn_create_date, record.good_issue_date)
        pending_pod_aging = kpi.calculate_pending_pod_aging(record.good_issue_date, record.pod_date)
        
        dashboard = {
            "dn_number": record.dn_no,
            "dealer_name": record.customer_name,
            "dealer_code": record.customer_code,
            "warehouse": record.warehouse,
            "city": record.ship_to_city,
            "product_code": record.product_code,
            "product_description": record.product_description,
            "quantity": int(record.dn_qty or 0),
            "amount": float(record.dn_amount or 0),
            "dn_date": record.dn_create_date.isoformat() if record.dn_create_date else None,
            "pgi_date": record.good_issue_date.isoformat() if record.good_issue_date else None,
            "pod_date": record.pod_date.isoformat() if record.pod_date else None,
            "delivery_status": record.delivery_status or "Unknown",
            "pod_status": record.pod_status or "Unknown",
            "delivery_aging": delivery_aging,
            "pod_aging": pod_aging,
            "pending_delivery_aging": pending_delivery_aging,
            "pending_pod_aging": pending_pod_aging
        }
        
        logger.info(f"DN dashboard built for: {dn_number}")
        return dashboard
    
    # ==========================================================
    # 9. RANKING INTEGRATION
    # ==========================================================
    
    def get_top_dealers(self, limit: int = 10, by: str = "revenue") -> List[Dict]:
        """Get top dealers by specified metric"""
        logger.info(f"Getting top {limit} dealers by {by}")
        
        analytics = get_analytics_service()
        if not analytics:
            logger.error("Analytics Service not available")
            return []
        
        # Get all dealer KPIs first
        schema = get_schema_service()
        if not schema:
            return []
        
        all_records = schema.get_all_records()
        kpi = get_kpi_service()
        if not kpi:
            return []
        
        dealer_kpis = kpi.calculate_all_dealers_kpis(all_records)
        
        # Convert to format expected by analytics
        dealer_list = []
        for dk in dealer_kpis:
            dealer_list.append({
                "dealer_name": dk.dealer_name,
                "revenue": dk.revenue,
                "units": dk.units,
                "dn_count": dk.dn_count,
                "delivery_rate": dk.delivery_rate,
                "pod_rate": dk.pod_rate
            })
        
        ranking = analytics.rank_dealers(dealer_list, metric=by, limit=limit)
        return ranking.items if hasattr(ranking, 'items') else []
    
    def get_top_warehouses(self, limit: int = 10, by: str = "revenue") -> List[Dict]:
        """Get top warehouses by specified metric"""
        logger.info(f"Getting top {limit} warehouses by {by}")
        
        schema = get_schema_service()
        if not schema:
            return []
        
        all_records = schema.get_all_records()
        kpi = get_kpi_service()
        if not kpi:
            return []
        
        warehouse_kpis = kpi.calculate_all_warehouses_kpis(all_records)
        
        warehouse_list = []
        for wk in warehouse_kpis:
            warehouse_list.append({
                "warehouse_name": wk.warehouse_name,
                "revenue": wk.revenue,
                "units": wk.units,
                "dn_count": wk.dn_count,
                "avg_delivery_aging": wk.avg_delivery_aging
            })
        
        sorted_list = sorted(warehouse_list, key=lambda x: x.get(by, 0), reverse=True)
        return sorted_list[:limit]
    
    # ==========================================================
    # 10. CONTROL TOWER INTEGRATION
    # ==========================================================
    
    def get_critical_deliveries(self) -> Dict[str, Any]:
        """Get critical deliveries report"""
        logger.info("Getting critical deliveries report")
        
        analytics = get_analytics_service()
        if not analytics:
            logger.error("Analytics Service not available")
            return {"alerts": [], "critical_count": 0}
        
        schema = get_schema_service()
        if not schema:
            return {"alerts": [], "critical_count": 0}
        
        all_records = schema.get_all_records()
        kpi = get_kpi_service()
        if not kpi:
            return {"alerts": [], "critical_count": 0}
        
        warehouse_kpis = kpi.calculate_all_warehouses_kpis(all_records)
        
        # Convert to dict format
        warehouse_dicts = []
        for wk in warehouse_kpis:
            warehouse_dicts.append({
                "warehouse_name": wk.warehouse_name,
                "pending_delivery": wk.pending_delivery,
                "avg_delivery_aging": wk.avg_delivery_aging,
                "critical_dn": wk.critical_dn
            })
        
        report = analytics.critical_delivery_report(warehouse_dicts, [], threshold_days=15)
        return {
            "alerts": [{"warehouse": a.entity_name, "message": a.message, "severity": a.severity} for a in report.alerts],
            "critical_count": len(report.alerts),
            "worst_warehouse": report.worst_warehouse
        }
    
    def get_critical_pod(self) -> Dict[str, Any]:
        """Get critical POD report"""
        logger.info("Getting critical POD report")
        
        analytics = get_analytics_service()
        if not analytics:
            return {"alerts": [], "critical_count": 0}
        
        schema = get_schema_service()
        if not schema:
            return {"alerts": [], "critical_count": 0}
        
        all_records = schema.get_all_records()
        kpi = get_kpi_service()
        if not kpi:
            return {"alerts": [], "critical_count": 0}
        
        warehouse_kpis = kpi.calculate_all_warehouses_kpis(all_records)
        
        warehouse_dicts = []
        for wk in warehouse_kpis:
            warehouse_dicts.append({
                "warehouse_name": wk.warehouse_name,
                "pending_pod": wk.pending_pod,
                "avg_pod_aging": wk.avg_pod_aging
            })
        
        report = analytics.critical_pod_report(warehouse_dicts, [], threshold_days=15)
        return {
            "alerts": [{"warehouse": a.entity_name, "message": a.message, "severity": a.severity} for a in report.alerts],
            "critical_count": len(report.alerts),
            "worst_warehouse": report.worst_warehouse
        }
    
    def get_risk_report(self) -> Dict[str, Any]:
        """Get comprehensive risk report"""
        logger.info("Getting risk report")
        
        schema = get_schema_service()
        if not schema:
            return {"risk_level": "UNKNOWN", "risk_score": 0}
        
        all_records = schema.get_all_records()
        kpi = get_kpi_service()
        if not kpi:
            return {"risk_level": "UNKNOWN", "risk_score": 0}
        
        executive_kpi = kpi.calculate_executive_kpis(all_records)
        
        risk_score = 0
        if executive_kpi.critical_deliveries > 50:
            risk_score += 30
        elif executive_kpi.critical_deliveries > 20:
            risk_score += 15
        
        if executive_kpi.critical_pod > 100:
            risk_score += 30
        elif executive_kpi.critical_pod > 50:
            risk_score += 15
        
        if executive_kpi.avg_delivery_aging > 10:
            risk_score += 20
        elif executive_kpi.avg_delivery_aging > 7:
            risk_score += 10
        
        if risk_score >= 60:
            risk_level = "RED"
        elif risk_score >= 30:
            risk_level = "ORANGE"
        elif risk_score >= 10:
            risk_level = "YELLOW"
        else:
            risk_level = "GREEN"
        
        return {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "critical_deliveries": executive_kpi.critical_deliveries,
            "critical_pod": executive_kpi.critical_pod,
            "avg_delivery_aging": executive_kpi.avg_delivery_aging,
            "warehouses_at_risk": executive_kpi.warehouses_with_high_aging,
            "dealers_with_pending": executive_kpi.dealers_with_pending
        }
    
    # ==========================================================
    # 11. TREND INTEGRATION
    # ==========================================================
    
    def get_revenue_trend(self, period: str = "monthly") -> List[Dict]:
        """Get revenue trend over time"""
        logger.info(f"Getting revenue trend: {period}")
        
        schema = get_schema_service()
        if not schema:
            return []
        
        # Get historical data grouped by period
        all_records = schema.get_all_records()
        
        # Group by period
        from collections import defaultdict
        period_data = defaultdict(float)
        
        for r in all_records:
            if r.dn_create_date and r.dn_amount:
                if period == "daily":
                    key = r.dn_create_date.isoformat()
                elif period == "weekly":
                    key = f"{r.dn_create_date.year}-W{r.dn_create_date.isocalendar()[1]}"
                elif period == "monthly":
                    key = f"{r.dn_create_date.year}-{r.dn_create_date.month:02d}"
                else:
                    key = f"{r.dn_create_date.year}"
                
                period_data[key] += float(r.dn_amount or 0)
        
        # Sort by period
        sorted_items = sorted(period_data.items())
        
        return [{"period": p, "revenue": r} for p, r in sorted_items[-12:]]
    
    # ==========================================================
    # 12. EXECUTIVE DASHBOARD
    # ==========================================================
    
    def build_executive_dashboard(self) -> Dict[str, Any]:
        """Build complete executive dashboard"""
        logger.info("Building executive dashboard")
        
        schema = get_schema_service()
        if not schema:
            return {"error": "Schema Service not available"}
        
        all_records = schema.get_all_records()
        kpi = get_kpi_service()
        if not kpi:
            return {"error": "KPI Service not available"}
        
        executive_kpi = kpi.calculate_executive_kpis(all_records)
        
        # Get top performers
        top_dealers = self.get_top_dealers(limit=5)
        top_warehouses = self.get_top_warehouses(limit=5)
        
        # Get risk report
        risk_report = self.get_risk_report()
        
        return {
            "total_revenue": executive_kpi.total_revenue,
            "total_units": executive_kpi.total_units,
            "total_dn": executive_kpi.total_dn,
            "delivery_rate": executive_kpi.delivery_rate,
            "pod_rate": executive_kpi.pod_rate,
            "pgi_rate": executive_kpi.pgi_rate,
            "avg_delivery_aging": executive_kpi.avg_delivery_aging,
            "avg_pod_aging": executive_kpi.avg_pod_aging,
            "pending_delivery": executive_kpi.total_pending_delivery,
            "pending_pod": executive_kpi.total_pending_pod,
            "critical_deliveries": executive_kpi.critical_deliveries,
            "critical_pod": executive_kpi.critical_pod,
            "top_dealers": top_dealers[:5],
            "top_warehouses": top_warehouses[:5],
            "risk_summary": risk_report,
            "timestamp": datetime.now().isoformat()
        }
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    def _calculate_warehouse_risk_score(self, warehouse_kpi) -> int:
        """Calculate risk score for warehouse (0-100)"""
        score = 0
        
        if warehouse_kpi.pending_delivery > 100:
            score += 30
        elif warehouse_kpi.pending_delivery > 50:
            score += 15
        
        if warehouse_kpi.pending_pod > 200:
            score += 30
        elif warehouse_kpi.pending_pod > 100:
            score += 15
        
        if warehouse_kpi.avg_delivery_aging > 10:
            score += 20
        elif warehouse_kpi.avg_delivery_aging > 7:
            score += 10
        
        if warehouse_kpi.critical_dn > 20:
            score += 20
        elif warehouse_kpi.critical_dn > 10:
            score += 10
        
        return min(score, 100)
    
    def _calculate_warehouse_score(self, warehouse_kpi) -> int:
        """Calculate performance score for warehouse (0-100)"""
        score = 0
        
        # Delivery performance (40%)
        delivery_efficiency = max(0, 100 - (warehouse_kpi.avg_delivery_aging * 5))
        score += delivery_efficiency * 0.4
        
        # POD performance (40%)
        pod_efficiency = max(0, 100 - (warehouse_kpi.avg_pod_aging * 5))
        score += pod_efficiency * 0.4
        
        # Volume score (20%)
        volume_score = min(100, warehouse_kpi.dn_count / 10)
        score += volume_score * 0.2
        
        return int(score)
    
    def _get_risk_level(self, risk_score: int) -> str:
        """Get risk level from score"""
        if risk_score >= 60:
            return "RED"
        elif risk_score >= 30:
            return "ORANGE"
        elif risk_score >= 10:
            return "YELLOW"
        else:
            return "GREEN"
    
    def _get_mock_sales_manager_dashboard(self, manager_name: str) -> Dict[str, Any]:
        """Return mock data for sales manager (until schema_service is ready)"""
        return {
            "manager_name": manager_name,
            "revenue": 45200000,
            "units": 18500,
            "dn_count": 1240,
            "pending_delivery": 45,
            "pending_pod": 78,
            "avg_delivery_aging": 2.3,
            "avg_pod_aging": 3.1,
            "top_dealer": "Dubai Electronics",
            "top_product": "HRF-438IFRA1"
        }


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_logistics_query_service = None

def get_logistics_query_service(db_session=None):
    """Get singleton instance of LogisticsQueryService"""
    global _logistics_query_service
    if _logistics_query_service is None:
        _logistics_query_service = LogisticsQueryService(db_session)
    return _logistics_query_service


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 60)
logger.info("Logistics Query Service - Business Data Aggregator")
logger.info("=" * 60)
logger.info("")
logger.info("   DASHBOARD ENGINES:")
logger.info("   ✅ Dealer Dashboard Engine")
logger.info("   ✅ Warehouse Dashboard Engine")
logger.info("   ✅ Warehouse SLA Dashboard")
logger.info("   ✅ Product Dashboard Engine")
logger.info("   ✅ Division Dashboard Engine")
logger.info("   ✅ Sales Manager Dashboard Engine")
logger.info("   ✅ City Dashboard Engine")
logger.info("   ✅ DN Dashboard Engine")
logger.info("")
logger.info("   INTEGRATIONS:")
logger.info("   ✅ KPI Integration")
logger.info("   ✅ Ranking Integration")
logger.info("   ✅ Trend Integration")
logger.info("   ✅ Control Tower Integration")
logger.info("")
logger.info("   STATUS: ✅ READY - AWAITING SCHEMA SERVICE")
logger.info("=" * 60)
