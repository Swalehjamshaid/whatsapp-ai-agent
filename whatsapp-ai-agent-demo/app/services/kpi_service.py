# ==========================================================
# FILE: app/services/kpi_service.py
# ==========================================================
# PURPOSE: Core Logistics Calculations Engine
#          ALL KPI calculations happen here - NO exceptions
#
# WHAT THIS FILE DOES:
# ✅ Delivery Aging (PGI Date - DN Date)
# ✅ Pending Delivery Aging (Today - DN Date when PGI NULL)
# ✅ POD Aging (POD Date - PGI Date)
# ✅ Pending POD Aging (Today - PGI Date when POD NULL)
# ✅ Full Cycle Time (POD Date - DN Date)
# ✅ Dealer KPI (Revenue, Units, DN, Delivery %, POD %, Aging)
# ✅ Warehouse KPI (Revenue, Units, DN, Delivery SLA, POD SLA)
# ✅ Warehouse Delivery SLA (Same Day, 1-5+ Day buckets)
# ✅ Warehouse POD SLA (Same Day, 1-5+ Day buckets)
# ✅ Sales Manager KPI
# ✅ Division KPI
# ✅ Executive KPI (Company-wide metrics)
#
# WHAT THIS FILE NEVER DOES:
# ✗ Query Database directly (receives data from schema_service)
# ✗ Send WhatsApp messages
# ✗ Parse user questions
# ✗ Format dashboards
# ✗ Handle HTTP requests
# ==========================================================

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from collections import defaultdict
from loguru import logger


# ==========================================================
# DATA CLASSES FOR KPI OUTPUTS
# ==========================================================

@dataclass
class DeliveryMetrics:
    """Core delivery metrics"""
    delivery_aging: Optional[int] = None
    pending_delivery_aging: Optional[int] = None
    pod_aging: Optional[int] = None
    pending_pod_aging: Optional[int] = None
    full_cycle: Optional[int] = None


@dataclass
class DealerKPIData:
    """Complete KPI data for a dealer"""
    dealer_name: str
    customer_code: str
    
    # Volume metrics
    revenue: float
    units: int
    dn_count: int
    
    # Delivery metrics
    delivered_dn: int
    pending_dn: int
    pgi_done: int
    pgi_pending: int
    
    # POD metrics
    pod_done: int
    pod_pending: int
    
    # Performance rates
    delivery_rate: float
    pod_rate: float
    pgi_rate: float
    completion_rate: float
    
    # Aging metrics
    avg_delivery_aging: float
    avg_pod_aging: float
    max_delivery_aging: int
    max_pod_aging: int
    min_delivery_aging: int
    min_pod_aging: int
    
    # Critical metrics
    critical_dn: int
    critical_pod: int


@dataclass
class WarehouseKPIData:
    """Complete KPI data for a warehouse"""
    warehouse_name: str
    
    # Volume metrics
    revenue: float
    units: int
    dn_count: int
    
    # Pending metrics
    pending_delivery: int
    pending_pod: int
    
    # Aging metrics
    avg_delivery_aging: float
    avg_pod_aging: float
    max_delivery_aging: int
    max_pod_aging: int
    
    # Critical metrics
    critical_dn: int
    
    # Delivery SLA buckets (PGI - DN)
    same_day_delivery: int
    one_day_delivery: int
    two_day_delivery: int
    three_day_delivery: int
    four_day_delivery: int
    five_plus_delivery: int
    
    # POD SLA buckets (POD - PGI)
    same_day_pod: int
    one_day_pod: int
    two_day_pod: int
    three_day_pod: int
    four_day_pod: int
    five_plus_pod: int


@dataclass
class SalesManagerKPIData:
    """KPI data for a sales manager"""
    manager_name: str
    revenue: float
    units: int
    dn_count: int
    pending_delivery: int
    pending_pod: int
    avg_delivery_aging: float
    avg_pod_aging: float
    top_dealer: str
    top_product: str


@dataclass
class DivisionKPIData:
    """KPI data for a product division"""
    division_name: str
    division_code: str
    revenue: float
    units: int
    dn_count: int
    market_share: float
    avg_delivery_aging: float
    avg_pod_aging: float
    top_product: str
    top_dealer: str


@dataclass
class ExecutiveKPIData:
    """Company-wide executive KPI data"""
    # Volume metrics
    total_revenue: float
    total_units: int
    total_dn: int
    
    # Delivery metrics
    total_delivered: int
    total_pending_delivery: int
    total_pending_pod: int
    
    # Performance rates
    delivery_rate: float
    pod_rate: float
    pgi_rate: float
    
    # Aging metrics
    avg_delivery_aging: float
    avg_pod_aging: float
    
    # Critical metrics
    critical_deliveries: int
    critical_pod: int
    
    # Warehouse summary
    total_warehouses: int
    warehouses_with_high_aging: int
    
    # Dealer summary
    total_dealers: int
    dealers_with_pending: int


# ==========================================================
# KPI SERVICE - MAIN CLASS
# ==========================================================

class KPIService:
    """
    Core Logistics Calculations Engine
    All KPI calculations happen here
    """
    
    def __init__(self):
        """Initialize the KPI Service"""
        logger.info("KPI Service initialized - Core Logistics Engine")
    
    # ==========================================================
    # CORE FORMULAS - Business Rules Engine
    # ==========================================================
    
    @staticmethod
    def calculate_delivery_aging(dn_date: Optional[date], pgi_date: Optional[date]) -> Optional[int]:
        """
        Formula: delivery_aging = PGI_Date - DN_Date
        
        Returns number of days between DN creation and PGI
        """
        if dn_date and pgi_date:
            return (pgi_date - dn_date).days
        return None
    
    @staticmethod
    def calculate_pending_delivery_aging(dn_date: Optional[date], pgi_date: Optional[date]) -> Optional[int]:
        """
        Formula: pending_delivery_aging = Today - DN_Date
        Condition: PGI_Date IS NULL
        
        Returns number of days DN has been waiting for PGI
        """
        if dn_date and not pgi_date:
            return (date.today() - dn_date).days
        return None
    
    @staticmethod
    def calculate_pod_aging(pgi_date: Optional[date], pod_date: Optional[date]) -> Optional[int]:
        """
        Formula: pod_aging = POD_Date - PGI_Date
        
        Returns number of days between PGI and POD
        """
        if pgi_date and pod_date:
            return (pod_date - pgi_date).days
        return None
    
    @staticmethod
    def calculate_pending_pod_aging(pgi_date: Optional[date], pod_date: Optional[date]) -> Optional[int]:
        """
        Formula: pending_pod_aging = Today - PGI_Date
        Condition: POD_Date IS NULL
        
        Returns number of days POD has been pending after PGI
        """
        if pgi_date and not pod_date:
            return (date.today() - pgi_date).days
        return None
    
    @staticmethod
    def calculate_full_cycle(dn_date: Optional[date], pod_date: Optional[date]) -> Optional[int]:
        """
        Formula: full_cycle = POD_Date - DN_Date
        
        Returns total days from DN creation to POD completion
        """
        if dn_date and pod_date:
            return (pod_date - dn_date).days
        return None
    
    @staticmethod
    def get_delivery_sla_bucket(aging_days: int) -> str:
        """
        Categorize delivery aging into SLA buckets
        """
        if aging_days == 0:
            return "same_day"
        elif aging_days == 1:
            return "one_day"
        elif aging_days == 2:
            return "two_day"
        elif aging_days == 3:
            return "three_day"
        elif aging_days == 4:
            return "four_day"
        else:
            return "five_plus"
    
    @staticmethod
    def get_pod_sla_bucket(aging_days: int) -> str:
        """
        Categorize POD aging into SLA buckets
        """
        if aging_days == 0:
            return "same_day"
        elif aging_days == 1:
            return "one_day"
        elif aging_days == 2:
            return "two_day"
        elif aging_days == 3:
            return "three_day"
        elif aging_days == 4:
            return "four_day"
        else:
            return "five_plus"
    
    @staticmethod
    def get_risk_severity(aging_days: int) -> str:
        """
        Determine risk severity based on aging days
        GREEN: 0-7 days
        YELLOW: 8-15 days
        ORANGE: 16-30 days
        RED: 31+ days
        """
        if aging_days <= 7:
            return "GREEN"
        elif aging_days <= 15:
            return "YELLOW"
        elif aging_days <= 30:
            return "ORANGE"
        else:
            return "RED"
    
    # ==========================================================
    # DEALER KPI ENGINE
    # ==========================================================
    
    def calculate_dealer_kpis(self, records: List[Any]) -> Optional[DealerKPIData]:
        """
        Calculate complete KPI data for a dealer
        
        Input: List of DeliveryReport records for a dealer
        Output: DealerKPIData with all calculated KPIs
        """
        if not records:
            return None
        
        today = date.today()
        
        # Get dealer name from first record
        dealer_name = records[0].customer_name or "Unknown"
        customer_code = records[0].customer_code or "N/A"
        
        # Unique DNs for this dealer
        unique_dns = set(r.dn_no for r in records)
        dn_count = len(unique_dns)
        
        # ==========================================================
        # VOLUME METRICS
        # ==========================================================
        revenue = sum(float(r.dn_amount or 0) for r in records)
        units = sum(int(r.dn_qty or 0) for r in records)
        
        # ==========================================================
        # DELIVERY METRICS (PGI)
        # ==========================================================
        pgi_done = sum(1 for r in records if r.good_issue_date is not None)
        pgi_pending = len(records) - pgi_done
        pgi_rate = (pgi_done / len(records) * 100) if records else 0
        
        # ==========================================================
        # DN STATUS (by unique DN)
        # ==========================================================
        delivered_dn = 0
        pending_dn = 0
        
        for dn in unique_dns:
            dn_records = [r for r in records if r.dn_no == dn]
            # Consider DN delivered if any record shows delivered
            if any(r.delivery_status == "Delivered" for r in dn_records):
                delivered_dn += 1
            else:
                pending_dn += 1
        
        # ==========================================================
        # POD METRICS
        # ==========================================================
        pod_done = sum(1 for r in records if r.pod_date is not None)
        pod_pending = len(records) - pod_done
        pod_rate = (pod_done / len(records) * 100) if records else 0
        
        completion_rate = (delivered_dn / dn_count * 100) if dn_count else 0
        
        # ==========================================================
        # AGING METRICS
        # ==========================================================
        delivery_agings = []
        pod_agings = []
        
        for r in records:
            # Delivery aging (PGI - DN)
            if r.dn_create_date and r.good_issue_date:
                aging = (r.good_issue_date - r.dn_create_date).days
                delivery_agings.append(aging)
            
            # POD aging (POD - PGI)
            if r.good_issue_date and r.pod_date:
                aging = (r.pod_date - r.good_issue_date).days
                pod_agings.append(aging)
        
        avg_delivery_aging = round(sum(delivery_agings) / len(delivery_agings), 1) if delivery_agings else 0
        avg_pod_aging = round(sum(pod_agings) / len(pod_agings), 1) if pod_agings else 0
        
        max_delivery_aging = max(delivery_agings) if delivery_agings else 0
        max_pod_aging = max(pod_agings) if pod_agings else 0
        min_delivery_aging = min(delivery_agings) if delivery_agings else 0
        min_pod_aging = min(pod_agings) if pod_agings else 0
        
        # ==========================================================
        # CRITICAL METRICS
        # ==========================================================
        critical_dn = 0
        critical_pod = 0
        
        for r in records:
            # Critical DN: No PGI and DN create date > 15 days
            if not r.good_issue_date and r.dn_create_date:
                if (today - r.dn_create_date).days > 15:
                    critical_dn += 1
            
            # Critical POD: PGI done but no POD, and PGI > 15 days
            if r.good_issue_date and not r.pod_date:
                if (today - r.good_issue_date).days > 15:
                    critical_pod += 1
        
        return DealerKPIData(
            dealer_name=dealer_name,
            customer_code=customer_code,
            revenue=revenue,
            units=units,
            dn_count=dn_count,
            delivered_dn=delivered_dn,
            pending_dn=pending_dn,
            pgi_done=pgi_done,
            pgi_pending=pgi_pending,
            pod_done=pod_done,
            pod_pending=pod_pending,
            delivery_rate=round((delivered_dn / dn_count * 100), 1) if dn_count else 0,
            pod_rate=round(pod_rate, 1),
            pgi_rate=round(pgi_rate, 1),
            completion_rate=round(completion_rate, 1),
            avg_delivery_aging=avg_delivery_aging,
            avg_pod_aging=avg_pod_aging,
            max_delivery_aging=max_delivery_aging,
            max_pod_aging=max_pod_aging,
            min_delivery_aging=min_delivery_aging,
            min_pod_aging=min_pod_aging,
            critical_dn=critical_dn,
            critical_pod=critical_pod
        )
    
    def calculate_all_dealers_kpis(self, all_records: List[Any]) -> List[DealerKPIData]:
        """
        Calculate KPIs for all dealers
        
        Groups records by dealer and calculates KPIs for each
        """
        # Group records by dealer
        dealer_records = defaultdict(list)
        for record in all_records:
            if record.customer_name:
                dealer_records[record.customer_name].append(record)
        
        # Calculate KPIs for each dealer
        dealer_kpis = []
        for dealer_name, records in dealer_records.items():
            kpi = self.calculate_dealer_kpis(records)
            if kpi:
                dealer_kpis.append(kpi)
        
        return dealer_kpis
    
    # ==========================================================
    # WAREHOUSE KPI ENGINE
    # ==========================================================
    
    def calculate_warehouse_kpis(self, records: List[Any]) -> Optional[WarehouseKPIData]:
        """
        Calculate complete KPI data for a warehouse
        
        Includes:
        - Volume metrics (revenue, units, DN count)
        - Pending metrics
        - Aging metrics
        - Delivery SLA buckets (PGI - DN)
        - POD SLA buckets (POD - PGI)
        """
        if not records:
            return None
        
        today = date.today()
        warehouse_name = records[0].warehouse or "Unknown"
        
        # Unique DNs
        unique_dns = set(r.dn_no for r in records)
        dn_count = len(unique_dns)
        
        # Volume metrics
        revenue = sum(float(r.dn_amount or 0) for r in records)
        units = sum(int(r.dn_qty or 0) for r in records)
        
        # Pending metrics
        pending_delivery = len([r for r in records if not r.good_issue_date])
        pending_pod = len([r for r in records if r.good_issue_date and not r.pod_date])
        
        # Aging metrics
        delivery_agings = []
        pod_agings = []
        
        for r in records:
            if r.dn_create_date and r.good_issue_date:
                aging = (r.good_issue_date - r.dn_create_date).days
                delivery_agings.append(aging)
            
            if r.good_issue_date and r.pod_date:
                aging = (r.pod_date - r.good_issue_date).days
                pod_agings.append(aging)
        
        avg_delivery_aging = round(sum(delivery_agings) / len(delivery_agings), 1) if delivery_agings else 0
        avg_pod_aging = round(sum(pod_agings) / len(pod_agings), 1) if pod_agings else 0
        max_delivery_aging = max(delivery_agings) if delivery_agings else 0
        max_pod_aging = max(pod_agings) if pod_agings else 0
        
        # Critical DN count
        critical_dn = len([r for r in records if not r.good_issue_date and r.dn_create_date 
                          and (today - r.dn_create_date).days > 15])
        
        # ==========================================================
        # DELIVERY SLA BUCKETS (PGI - DN)
        # ==========================================================
        delivery_buckets = {
            "same_day": 0, "one_day": 0, "two_day": 0,
            "three_day": 0, "four_day": 0, "five_plus": 0
        }
        
        for aging in delivery_agings:
            bucket = self.get_delivery_sla_bucket(aging)
            delivery_buckets[bucket] += 1
        
        # ==========================================================
        # POD SLA BUCKETS (POD - PGI)
        # ==========================================================
        pod_buckets = {
            "same_day": 0, "one_day": 0, "two_day": 0,
            "three_day": 0, "four_day": 0, "five_plus": 0
        }
        
        for aging in pod_agings:
            bucket = self.get_pod_sla_bucket(aging)
            pod_buckets[bucket] += 1
        
        return WarehouseKPIData(
            warehouse_name=warehouse_name,
            revenue=revenue,
            units=units,
            dn_count=dn_count,
            pending_delivery=pending_delivery,
            pending_pod=pending_pod,
            avg_delivery_aging=avg_delivery_aging,
            avg_pod_aging=avg_pod_aging,
            max_delivery_aging=max_delivery_aging,
            max_pod_aging=max_pod_aging,
            critical_dn=critical_dn,
            same_day_delivery=delivery_buckets["same_day"],
            one_day_delivery=delivery_buckets["one_day"],
            two_day_delivery=delivery_buckets["two_day"],
            three_day_delivery=delivery_buckets["three_day"],
            four_day_delivery=delivery_buckets["four_day"],
            five_plus_delivery=delivery_buckets["five_plus"],
            same_day_pod=pod_buckets["same_day"],
            one_day_pod=pod_buckets["one_day"],
            two_day_pod=pod_buckets["two_day"],
            three_day_pod=pod_buckets["three_day"],
            four_day_pod=pod_buckets["four_day"],
            five_plus_pod=pod_buckets["five_plus"]
        )
    
    def calculate_all_warehouses_kpis(self, all_records: List[Any]) -> List[WarehouseKPIData]:
        """
        Calculate KPIs for all warehouses
        
        Groups records by warehouse and calculates KPIs for each
        """
        warehouse_records = defaultdict(list)
        for record in all_records:
            if record.warehouse:
                warehouse_records[record.warehouse].append(record)
        
        warehouse_kpis = []
        for warehouse_name, records in warehouse_records.items():
            kpi = self.calculate_warehouse_kpis(records)
            if kpi:
                warehouse_kpis.append(kpi)
        
        return warehouse_kpis
    
    # ==========================================================
    # CITY KPI ENGINE
    # ==========================================================
    
    def calculate_city_kpis(self, records: List[Any]) -> Dict[str, Any]:
        """Calculate KPIs for a city"""
        if not records:
            return {}
        
        city_name = records[0].ship_to_city or "Unknown"
        
        revenue = sum(float(r.dn_amount or 0) for r in records)
        units = sum(int(r.dn_qty or 0) for r in records)
        dn_count = len(set(r.dn_no for r in records))
        
        pending_delivery = len([r for r in records if not r.good_issue_date])
        pending_pod = len([r for r in records if r.good_issue_date and not r.pod_date])
        
        # Delivery aging
        delivery_agings = []
        for r in records:
            if r.dn_create_date and r.good_issue_date:
                delivery_agings.append((r.good_issue_date - r.dn_create_date).days)
        
        avg_delivery_aging = round(sum(delivery_agings) / len(delivery_agings), 1) if delivery_agings else 0
        
        # Delivery rate
        delivered = len([r for r in records if r.delivery_status == "Delivered"])
        delivery_rate = (delivered / len(records) * 100) if records else 0
        
        return {
            "city_name": city_name,
            "revenue": revenue,
            "units": units,
            "dn_count": dn_count,
            "pending_delivery": pending_delivery,
            "pending_pod": pending_pod,
            "avg_delivery_aging": avg_delivery_aging,
            "delivery_rate": round(delivery_rate, 1)
        }
    
    def calculate_all_cities_kpis(self, all_records: List[Any]) -> List[Dict]:
        """Calculate KPIs for all cities"""
        city_records = defaultdict(list)
        for record in all_records:
            if record.ship_to_city:
                city_records[record.ship_to_city].append(record)
        
        city_kpis = []
        for city_name, records in city_records.items():
            kpi = self.calculate_city_kpis(records)
            if kpi:
                city_kpis.append(kpi)
        
        return sorted(city_kpis, key=lambda x: x.get("revenue", 0), reverse=True)
    
    # ==========================================================
    # PRODUCT KPI ENGINE
    # ==========================================================
    
    def calculate_product_kpis(self, records: List[Any]) -> Dict[str, Any]:
        """Calculate KPIs for a product"""
        if not records:
            return {}
        
        product_code = records[0].product_code or "Unknown"
        product_name = records[0].product_description or product_code
        
        revenue = sum(float(r.dn_amount or 0) for r in records)
        units = sum(int(r.dn_qty or 0) for r in records)
        dn_count = len(set(r.dn_no for r in records))
        
        # Delivery aging for this product
        delivery_agings = []
        for r in records:
            if r.dn_create_date and r.good_issue_date:
                delivery_agings.append((r.good_issue_date - r.dn_create_date).days)
        
        avg_delivery_aging = round(sum(delivery_agings) / len(delivery_agings), 1) if delivery_agings else 0
        
        return {
            "product_code": product_code,
            "product_name": product_name,
            "revenue": revenue,
            "units": units,
            "dn_count": dn_count,
            "avg_delivery_aging": avg_delivery_aging
        }
    
    def calculate_all_products_kpis(self, all_records: List[Any]) -> List[Dict]:
        """Calculate KPIs for all products"""
        product_records = defaultdict(list)
        for record in all_records:
            if record.product_code:
                product_records[record.product_code].append(record)
        
        product_kpis = []
        for product_code, records in product_records.items():
            kpi = self.calculate_product_kpis(records)
            if kpi:
                product_kpis.append(kpi)
        
        return sorted(product_kpis, key=lambda x: x.get("units", 0), reverse=True)
    
    # ==========================================================
    # DIVISION KPI ENGINE
    # ==========================================================
    
    def calculate_division_kpis(self, records: List[Any], division_name: str, division_code: str) -> DivisionKPIData:
        """Calculate KPIs for a product division"""
        if not records:
            return DivisionKPIData(
                division_name=division_name,
                division_code=division_code,
                revenue=0,
                units=0,
                dn_count=0,
                market_share=0,
                avg_delivery_aging=0,
                avg_pod_aging=0,
                top_product="N/A",
                top_dealer="N/A"
            )
        
        revenue = sum(float(r.dn_amount or 0) for r in records)
        units = sum(int(r.dn_qty or 0) for r in records)
        dn_count = len(set(r.dn_no for r in records))
        
        # Aging metrics
        delivery_agings = []
        pod_agings = []
        
        for r in records:
            if r.dn_create_date and r.good_issue_date:
                delivery_agings.append((r.good_issue_date - r.dn_create_date).days)
            if r.good_issue_date and r.pod_date:
                pod_agings.append((r.pod_date - r.good_issue_date).days)
        
        avg_delivery_aging = round(sum(delivery_agings) / len(delivery_agings), 1) if delivery_agings else 0
        avg_pod_aging = round(sum(pod_agings) / len(pod_agings), 1) if pod_agings else 0
        
        # Find top product in this division
        product_units = defaultdict(int)
        for r in records:
            product = r.product_description or r.product_code or "Unknown"
            product_units[product] += int(r.dn_qty or 0)
        
        top_product = max(product_units.items(), key=lambda x: x[1])[0] if product_units else "N/A"
        
        # Find top dealer in this division
        dealer_units = defaultdict(int)
        for r in records:
            if r.customer_name:
                dealer_units[r.customer_name] += int(r.dn_qty or 0)
        
        top_dealer = max(dealer_units.items(), key=lambda x: x[1])[0] if dealer_units else "N/A"
        
        return DivisionKPIData(
            division_name=division_name,
            division_code=division_code,
            revenue=revenue,
            units=units,
            dn_count=dn_count,
            market_share=0,  # Would need total market data
            avg_delivery_aging=avg_delivery_aging,
            avg_pod_aging=avg_pod_aging,
            top_product=top_product,
            top_dealer=top_dealer
        )
    
    # ==========================================================
    # EXECUTIVE KPI ENGINE
    # ==========================================================
    
    def calculate_executive_kpis(self, all_records: List[Any]) -> ExecutiveKPIData:
        """
        Calculate company-wide executive KPIs
        
        This is the master KPI function that aggregates everything
        """
        if not all_records:
            return ExecutiveKPIData(
                total_revenue=0, total_units=0, total_dn=0,
                total_delivered=0, total_pending_delivery=0, total_pending_pod=0,
                delivery_rate=0, pod_rate=0, pgi_rate=0,
                avg_delivery_aging=0, avg_pod_aging=0,
                critical_deliveries=0, critical_pod=0,
                total_warehouses=0, warehouses_with_high_aging=0,
                total_dealers=0, dealers_with_pending=0
            )
        
        today = date.today()
        
        # Volume metrics
        total_revenue = sum(float(r.dn_amount or 0) for r in all_records)
        total_units = sum(int(r.dn_qty or 0) for r in all_records)
        total_dn = len(set(r.dn_no for r in all_records))
        
        # Delivery metrics
        total_delivered = len([r for r in all_records if r.delivery_status == "Delivered"])
        total_pending_delivery = len([r for r in all_records if not r.good_issue_date])
        total_pending_pod = len([r for r in all_records if r.good_issue_date and not r.pod_date])
        
        delivery_rate = (total_delivered / len(all_records) * 100) if all_records else 0
        pod_rate = (len([r for r in all_records if r.pod_date]) / len(all_records) * 100) if all_records else 0
        pgi_rate = (len([r for r in all_records if r.good_issue_date]) / len(all_records) * 100) if all_records else 0
        
        # Aging metrics
        delivery_agings = []
        pod_agings = []
        
        for r in all_records:
            if r.dn_create_date and r.good_issue_date:
                delivery_agings.append((r.good_issue_date - r.dn_create_date).days)
            if r.good_issue_date and r.pod_date:
                pod_agings.append((r.pod_date - r.good_issue_date).days)
        
        avg_delivery_aging = round(sum(delivery_agings) / len(delivery_agings), 1) if delivery_agings else 0
        avg_pod_aging = round(sum(pod_agings) / len(pod_agings), 1) if pod_agings else 0
        
        # Critical metrics
        critical_deliveries = len([r for r in all_records 
                                   if not r.good_issue_date and r.dn_create_date 
                                   and (today - r.dn_create_date).days > 15])
        critical_pod = len([r for r in all_records 
                           if r.good_issue_date and not r.pod_date 
                           and (today - r.good_issue_date).days > 15])
        
        # Warehouse summary
        warehouses = set(r.warehouse for r in all_records if r.warehouse)
        warehouses_with_high_aging = 0
        
        for wh in warehouses:
            wh_records = [r for r in all_records if r.warehouse == wh]
            wh_agings = [(r.good_issue_date - r.dn_create_date).days 
                        for r in wh_records if r.dn_create_date and r.good_issue_date]
            if wh_agings and sum(wh_agings) / len(wh_agings) > 10:
                warehouses_with_high_aging += 1
        
        # Dealer summary
        dealers = set(r.customer_name for r in all_records if r.customer_name)
        dealers_with_pending = len([d for d in dealers 
                                   if any(not r.good_issue_date for r in all_records if r.customer_name == d)])
        
        return ExecutiveKPIData(
            total_revenue=total_revenue,
            total_units=total_units,
            total_dn=total_dn,
            total_delivered=total_delivered,
            total_pending_delivery=total_pending_delivery,
            total_pending_pod=total_pending_pod,
            delivery_rate=round(delivery_rate, 1),
            pod_rate=round(pod_rate, 1),
            pgi_rate=round(pgi_rate, 1),
            avg_delivery_aging=avg_delivery_aging,
            avg_pod_aging=avg_pod_aging,
            critical_deliveries=critical_deliveries,
            critical_pod=critical_pod,
            total_warehouses=len(warehouses),
            warehouses_with_high_aging=warehouses_with_high_aging,
            total_dealers=len(dealers),
            dealers_with_pending=dealers_with_pending
        )
    
    # ==========================================================
    # SALES MANAGER KPI ENGINE
    # ==========================================================
    
    def calculate_sales_manager_kpis(self, records: List[Any], manager_name: str) -> SalesManagerKPIData:
        """Calculate KPIs for a sales manager"""
        if not records:
            return SalesManagerKPIData(
                manager_name=manager_name,
                revenue=0,
                units=0,
                dn_count=0,
                pending_delivery=0,
                pending_pod=0,
                avg_delivery_aging=0,
                avg_pod_aging=0,
                top_dealer="N/A",
                top_product="N/A"
            )
        
        revenue = sum(float(r.dn_amount or 0) for r in records)
        units = sum(int(r.dn_qty or 0) for r in records)
        dn_count = len(set(r.dn_no for r in records))
        
        pending_delivery = len([r for r in records if not r.good_issue_date])
        pending_pod = len([r for r in records if r.good_issue_date and not r.pod_date])
        
        # Aging metrics
        delivery_agings = []
        pod_agings = []
        
        for r in records:
            if r.dn_create_date and r.good_issue_date:
                delivery_agings.append((r.good_issue_date - r.dn_create_date).days)
            if r.good_issue_date and r.pod_date:
                pod_agings.append((r.pod_date - r.good_issue_date).days)
        
        avg_delivery_aging = round(sum(delivery_agings) / len(delivery_agings), 1) if delivery_agings else 0
        avg_pod_aging = round(sum(pod_agings) / len(pod_agings), 1) if pod_agings else 0
        
        # Find top dealer
        dealer_revenue = defaultdict(float)
        for r in records:
            if r.customer_name:
                dealer_revenue[r.customer_name] += float(r.dn_amount or 0)
        top_dealer = max(dealer_revenue.items(), key=lambda x: x[1])[0] if dealer_revenue else "N/A"
        
        # Find top product
        product_units = defaultdict(int)
        for r in records:
            product = r.product_description or r.product_code or "Unknown"
            product_units[product] += int(r.dn_qty or 0)
        top_product = max(product_units.items(), key=lambda x: x[1])[0] if product_units else "N/A"
        
        return SalesManagerKPIData(
            manager_name=manager_name,
            revenue=revenue,
            units=units,
            dn_count=dn_count,
            pending_delivery=pending_delivery,
            pending_pod=pending_pod,
            avg_delivery_aging=avg_delivery_aging,
            avg_pod_aging=avg_pod_aging,
            top_dealer=top_dealer,
            top_product=top_product
        )


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_kpi_service = None

def get_kpi_service() -> KPIService:
    """Get singleton instance of KPIService"""
    global _kpi_service
    if _kpi_service is None:
        _kpi_service = KPIService()
    return _kpi_service


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 60)
logger.info("KPI Service - Core Logistics Calculations Engine")
logger.info("=" * 60)
logger.info("")
logger.info("   FORMULAS IMPLEMENTED:")
logger.info("   ✅ Delivery Aging = PGI Date - DN Date")
logger.info("   ✅ Pending Delivery Aging = Today - DN Date (PGI NULL)")
logger.info("   ✅ POD Aging = POD Date - PGI Date")
logger.info("   ✅ Pending POD Aging = Today - PGI Date (POD NULL)")
logger.info("   ✅ Full Cycle = POD Date - DN Date")
logger.info("")
logger.info("   KPI ENGINES:")
logger.info("   ✅ Dealer KPI Engine")
logger.info("   ✅ Warehouse KPI Engine")
logger.info("   ✅ City KPI Engine")
logger.info("   ✅ Product KPI Engine")
logger.info("   ✅ Division KPI Engine")
logger.info("   ✅ Executive KPI Engine")
logger.info("   ✅ Sales Manager KPI Engine")
logger.info("")
logger.info("   SLA BUCKETS:")
logger.info("   ✅ Delivery SLA (Same Day, 1-5+ Days)")
logger.info("   ✅ POD SLA (Same Day, 1-5+ Days)")
logger.info("")
logger.info("   RISK SEVERITY:")
logger.info("   ✅ GREEN (0-7 days)")
logger.info("   ✅ YELLOW (8-15 days)")
logger.info("   ✅ ORANGE (16-30 days)")
logger.info("   ✅ RED (31+ days)")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 60)
