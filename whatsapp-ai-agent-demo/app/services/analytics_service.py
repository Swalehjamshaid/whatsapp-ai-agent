# ==========================================================
# FILE: app/services/analytics_service.py
# ==========================================================
# PURPOSE: Business Intelligence & Control Tower Engine
#          Transforms KPI Data → Insights, Rankings, Comparisons, Trends
#
# WHAT THIS FILE DOES:
# ✅ Rankings (Top/Worst Dealers, Warehouses, Products, Cities, Divisions)
# ✅ Comparisons (Dealer vs Dealer, City vs City, Product vs Product)
# ✅ Trend Analysis (Revenue, Units, POD, Delivery trends over time)
# ✅ Control Tower (Critical alerts, risk reports, worst performers)
# ✅ Executive Dashboard (Company-wide KPIs, top performers)
# ✅ Root Cause Preparation (Data for GROQ analysis)
# ✅ Performance Scoring (Score dealers/warehouses by metrics)
# ✅ SLA Analytics (Delivery/POD bucket analysis)
# ✅ Alert Generation (Proactive alerts based on thresholds)
#
# WHAT THIS FILE NEVER DOES:
# ✗ Send WhatsApp Messages
# ✗ Parse User Questions
# ✗ Detect Intents
# ✗ Query Database Directly
# ✗ Manage Authentication
# ✗ Receive HTTP Requests
# ==========================================================

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from collections import defaultdict
from loguru import logger


# ==========================================================
# DATA CLASSES
# ==========================================================

@dataclass
class RankedItem:
    """Single item in ranking results"""
    name: str
    value: float
    metric: str
    rank: int


@dataclass
class RankingReport:
    """Complete ranking report"""
    title: str
    dimension: str
    metric: str
    items: List[RankedItem]
    total_items: int
    generated_at: str


@dataclass
class ComparisonResult:
    """Side-by-side comparison result"""
    entity_a: str
    entity_b: str
    metric: str
    a_value: float
    b_value: float
    difference: float
    percent_difference: float
    winner: str
    winning_margin: float


@dataclass
class TrendPoint:
    """Single point in trend analysis"""
    period: str
    value: float
    growth_rate: Optional[float] = None


@dataclass
class TrendReport:
    """Complete trend report"""
    metric: str
    dimension: str
    granularity: str
    points: List[TrendPoint]
    overall_growth: float
    best_period: str
    worst_period: str


@dataclass
class ExecutiveDashboard:
    """Executive-level dashboard data"""
    # Volume metrics
    total_revenue: float
    total_units: int
    total_dns: int
    total_delivered: int
    total_pending_delivery: int
    total_pending_pod: int
    
    # Performance rates
    delivery_rate: float
    pod_rate: float
    pgi_rate: float
    
    # Aging averages
    avg_delivery_aging: float
    avg_pod_aging: float
    
    # Top performers
    top_dealer: str
    top_warehouse: str
    top_product: str
    top_city: str
    top_division: str
    
    # Risk indicators
    critical_deliveries: int
    critical_pod: int
    red_risk_locations: List[str]
    
    # Summary
    risk_summary: str
    health_score: float


@dataclass
class ControlTowerAlert:
    """Single control tower alert"""
    severity: str  # GREEN, YELLOW, ORANGE, RED
    category: str  # delivery, pod, revenue, quality
    entity_type: str  # warehouse, dealer, city, product
    entity_name: str
    message: str
    metric_value: float
    threshold: float
    days_at_risk: Optional[int] = None


@dataclass
class ControlTowerReport:
    """Complete control tower report"""
    alerts: List[ControlTowerAlert]
    risk_summary: Dict[str, int]
    worst_warehouse: str
    worst_dealer: str
    worst_city: str
    oldest_pending_delivery_days: int
    oldest_pending_pod_days: int
    generated_at: str


@dataclass
class PerformanceScore:
    """Performance score for an entity"""
    entity_name: str
    entity_type: str
    score: float
    grade: str  # A, B, C, D, F
    breakdown: Dict[str, float]


@dataclass
class SLAAnalysis:
    """SLA bucket analysis"""
    entity_name: str
    same_day: int
    one_day: int
    two_day: int
    three_day: int
    four_day: int
    five_plus: int
    average_days: float


# ==========================================================
# RISK BUCKETS (from kpi_service)
# ==========================================================

class RiskBuckets:
    DELIVERY_AGING = {
        "GREEN": (0, 7),
        "YELLOW": (8, 15),
        "ORANGE": (16, 30),
        "RED": (31, float('inf'))
    }
    
    POD_AGING = {
        "GREEN": (0, 7),
        "YELLOW": (8, 15),
        "ORANGE": (16, 30),
        "RED": (31, float('inf'))
    }


# ==========================================================
# ANALYTICS SERVICE
# ==========================================================

class AnalyticsService:
    """
    Business Intelligence & Control Tower Engine
    Transforms KPI data into actionable insights
    """
    
    def __init__(self):
        """Initialize the Analytics Service"""
        logger.info("Analytics Service initialized")
    
    # ==========================================================
    # MODULE 1: RANKING ENGINE
    # ==========================================================
    
    def rank_dealers(self, dealer_kpis: List[Dict], metric: str = "revenue", 
                     limit: int = 10, reverse: bool = True) -> RankingReport:
        """
        Rank dealers by specified metric
        
        Args:
            dealer_kpis: List of dealer KPI dictionaries
            metric: revenue, units, dn_count, delivery_rate, pod_rate, delivery_aging, pod_aging
            limit: Number of results to return
            reverse: True for highest first, False for lowest first
        """
        if not dealer_kpis:
            return RankingReport(
                title="Top Dealers",
                dimension="dealer",
                metric=metric,
                items=[],
                total_items=0,
                generated_at=datetime.now().isoformat()
            )
        
        # Sort by metric
        sorted_dealers = sorted(dealer_kpis, key=lambda x: x.get(metric, 0), reverse=reverse)
        top_dealers = sorted_dealers[:limit]
        
        items = []
        for i, dealer in enumerate(top_dealers, 1):
            items.append(RankedItem(
                name=dealer.get("dealer_name", "Unknown"),
                value=dealer.get(metric, 0),
                metric=metric,
                rank=i
            ))
        
        title = "Top Dealers" if reverse else "Bottom Dealers"
        
        return RankingReport(
            title=f"{title} by {metric.replace('_', ' ').title()}",
            dimension="dealer",
            metric=metric,
            items=items,
            total_items=len(dealer_kpis),
            generated_at=datetime.now().isoformat()
        )
    
    def rank_warehouses(self, warehouse_kpis: List[Dict], metric: str = "revenue",
                        limit: int = 10, reverse: bool = True) -> RankingReport:
        """Rank warehouses by specified metric"""
        if not warehouse_kpis:
            return RankingReport(
                title="Top Warehouses",
                dimension="warehouse",
                metric=metric,
                items=[],
                total_items=0,
                generated_at=datetime.now().isoformat()
            )
        
        sorted_warehouses = sorted(warehouse_kpis, key=lambda x: x.get(metric, 0), reverse=reverse)
        top_warehouses = sorted_warehouses[:limit]
        
        items = []
        for i, wh in enumerate(top_warehouses, 1):
            items.append(RankedItem(
                name=wh.get("warehouse_name", "Unknown"),
                value=wh.get(metric, 0),
                metric=metric,
                rank=i
            ))
        
        title = "Top Warehouses" if reverse else "Bottom Warehouses"
        
        return RankingReport(
            title=f"{title} by {metric.replace('_', ' ').title()}",
            dimension="warehouse",
            metric=metric,
            items=items,
            total_items=len(warehouse_kpis),
            generated_at=datetime.now().isoformat()
        )
    
    def rank_products(self, product_kpis: List[Dict], metric: str = "units",
                      limit: int = 10, reverse: bool = True) -> RankingReport:
        """Rank products by specified metric"""
        if not product_kpis:
            return RankingReport(
                title="Top Products",
                dimension="product",
                metric=metric,
                items=[],
                total_items=0,
                generated_at=datetime.now().isoformat()
            )
        
        sorted_products = sorted(product_kpis, key=lambda x: x.get(metric, 0), reverse=reverse)
        top_products = sorted_products[:limit]
        
        items = []
        for i, prod in enumerate(top_products, 1):
            items.append(RankedItem(
                name=prod.get("product_name", prod.get("product_code", "Unknown")),
                value=prod.get(metric, 0),
                metric=metric,
                rank=i
            ))
        
        title = "Top Products" if reverse else "Bottom Products"
        
        return RankingReport(
            title=f"{title} by {metric.replace('_', ' ').title()}",
            dimension="product",
            metric=metric,
            items=items,
            total_items=len(product_kpis),
            generated_at=datetime.now().isoformat()
        )
    
    def rank_cities(self, city_kpis: List[Dict], metric: str = "revenue",
                    limit: int = 10, reverse: bool = True) -> RankingReport:
        """Rank cities by specified metric"""
        if not city_kpis:
            return RankingReport(
                title="Top Cities",
                dimension="city",
                metric=metric,
                items=[],
                total_items=0,
                generated_at=datetime.now().isoformat()
            )
        
        sorted_cities = sorted(city_kpis, key=lambda x: x.get(metric, 0), reverse=reverse)
        top_cities = sorted_cities[:limit]
        
        items = []
        for i, city in enumerate(top_cities, 1):
            items.append(RankedItem(
                name=city.get("city_name", "Unknown"),
                value=city.get(metric, 0),
                metric=metric,
                rank=i
            ))
        
        title = "Top Cities" if reverse else "Bottom Cities"
        
        return RankingReport(
            title=f"{title} by {metric.replace('_', ' ').title()}",
            dimension="city",
            metric=metric,
            items=items,
            total_items=len(city_kpis),
            generated_at=datetime.now().isoformat()
        )
    
    def rank_divisions(self, division_kpis: List[Dict], metric: str = "revenue",
                       limit: int = 10, reverse: bool = True) -> RankingReport:
        """Rank divisions by specified metric"""
        if not division_kpis:
            return RankingReport(
                title="Top Divisions",
                dimension="division",
                metric=metric,
                items=[],
                total_items=0,
                generated_at=datetime.now().isoformat()
            )
        
        sorted_divisions = sorted(division_kpis, key=lambda x: x.get(metric, 0), reverse=reverse)
        top_divisions = sorted_divisions[:limit]
        
        items = []
        for i, div in enumerate(top_divisions, 1):
            items.append(RankedItem(
                name=div.get("division_name", "Unknown"),
                value=div.get(metric, 0),
                metric=metric,
                rank=i
            ))
        
        title = "Top Divisions" if reverse else "Bottom Divisions"
        
        return RankingReport(
            title=f"{title} by {metric.replace('_', ' ').title()}",
            dimension="division",
            metric=metric,
            items=items,
            total_items=len(division_kpis),
            generated_at=datetime.now().isoformat()
        )
    
    # ==========================================================
    # MODULE 2: COMPARISON ENGINE
    # ==========================================================
    
    def compare_dealers(self, dealer_a_kpi: Dict, dealer_b_kpi: Dict, 
                        metric: str = "revenue") -> ComparisonResult:
        """Compare two dealers side by side"""
        
        value_a = dealer_a_kpi.get(metric, 0)
        value_b = dealer_b_kpi.get(metric, 0)
        
        difference = value_a - value_b
        percent_difference = (difference / value_b * 100) if value_b > 0 else 0
        winner = dealer_a_kpi.get("dealer_name") if value_a > value_b else dealer_b_kpi.get("dealer_name")
        winning_margin = abs(difference / max(value_a, value_b) * 100) if max(value_a, value_b) > 0 else 0
        
        return ComparisonResult(
            entity_a=dealer_a_kpi.get("dealer_name", "Unknown"),
            entity_b=dealer_b_kpi.get("dealer_name", "Unknown"),
            metric=metric,
            a_value=value_a,
            b_value=value_b,
            difference=difference,
            percent_difference=percent_difference,
            winner=winner,
            winning_margin=winning_margin
        )
    
    def compare_warehouses(self, warehouse_a_kpi: Dict, warehouse_b_kpi: Dict,
                           metric: str = "revenue") -> ComparisonResult:
        """Compare two warehouses side by side"""
        
        value_a = warehouse_a_kpi.get(metric, 0)
        value_b = warehouse_b_kpi.get(metric, 0)
        
        difference = value_a - value_b
        percent_difference = (difference / value_b * 100) if value_b > 0 else 0
        winner = warehouse_a_kpi.get("warehouse_name") if value_a > value_b else warehouse_b_kpi.get("warehouse_name")
        winning_margin = abs(difference / max(value_a, value_b) * 100) if max(value_a, value_b) > 0 else 0
        
        return ComparisonResult(
            entity_a=warehouse_a_kpi.get("warehouse_name", "Unknown"),
            entity_b=warehouse_b_kpi.get("warehouse_name", "Unknown"),
            metric=metric,
            a_value=value_a,
            b_value=value_b,
            difference=difference,
            percent_difference=percent_difference,
            winner=winner,
            winning_margin=winning_margin
        )
    
    def compare_cities(self, city_a_kpi: Dict, city_b_kpi: Dict,
                       metric: str = "revenue") -> ComparisonResult:
        """Compare two cities side by side"""
        
        value_a = city_a_kpi.get(metric, 0)
        value_b = city_b_kpi.get(metric, 0)
        
        difference = value_a - value_b
        percent_difference = (difference / value_b * 100) if value_b > 0 else 0
        winner = city_a_kpi.get("city_name") if value_a > value_b else city_b_kpi.get("city_name")
        winning_margin = abs(difference / max(value_a, value_b) * 100) if max(value_a, value_b) > 0 else 0
        
        return ComparisonResult(
            entity_a=city_a_kpi.get("city_name", "Unknown"),
            entity_b=city_b_kpi.get("city_name", "Unknown"),
            metric=metric,
            a_value=value_a,
            b_value=value_b,
            difference=difference,
            percent_difference=percent_difference,
            winner=winner,
            winning_margin=winning_margin
        )
    
    def compare_products(self, product_a_kpi: Dict, product_b_kpi: Dict,
                         metric: str = "units") -> ComparisonResult:
        """Compare two products side by side"""
        
        value_a = product_a_kpi.get(metric, 0)
        value_b = product_b_kpi.get(metric, 0)
        
        difference = value_a - value_b
        percent_difference = (difference / value_b * 100) if value_b > 0 else 0
        winner = product_a_kpi.get("product_name", "Unknown") if value_a > value_b else product_b_kpi.get("product_name", "Unknown")
        winning_margin = abs(difference / max(value_a, value_b) * 100) if max(value_a, value_b) > 0 else 0
        
        return ComparisonResult(
            entity_a=product_a_kpi.get("product_name", product_a_kpi.get("product_code", "Unknown")),
            entity_b=product_b_kpi.get("product_name", product_b_kpi.get("product_code", "Unknown")),
            metric=metric,
            a_value=value_a,
            b_value=value_b,
            difference=difference,
            percent_difference=percent_difference,
            winner=winner,
            winning_margin=winning_margin
        )
    
    # ==========================================================
    # MODULE 3: TREND ENGINE
    # ==========================================================
    
    def revenue_trend(self, historical_data: List[Dict], period: str = "monthly") -> TrendReport:
        """
        Analyze revenue trend over time
        
        Args:
            historical_data: List of dicts with 'period' and 'revenue' keys
            period: daily, weekly, monthly, quarterly, yearly
        """
        return self._generate_trend(historical_data, "revenue", period)
    
    def unit_trend(self, historical_data: List[Dict], period: str = "monthly") -> TrendReport:
        """Analyze unit sales trend over time"""
        return self._generate_trend(historical_data, "units", period)
    
    def dn_trend(self, historical_data: List[Dict], period: str = "monthly") -> TrendReport:
        """Analyze DN count trend over time"""
        return self._generate_trend(historical_data, "dn_count", period)
    
    def delivery_trend(self, historical_data: List[Dict], period: str = "monthly") -> TrendReport:
        """Analyze delivery rate trend over time"""
        return self._generate_trend(historical_data, "delivery_rate", period)
    
    def pod_trend(self, historical_data: List[Dict], period: str = "monthly") -> TrendReport:
        """Analyze POD rate trend over time"""
        return self._generate_trend(historical_data, "pod_rate", period)
    
    def _generate_trend(self, data: List[Dict], metric: str, period: str) -> TrendReport:
        """Generic trend generation"""
        if not data:
            return TrendReport(
                metric=metric,
                dimension="overall",
                granularity=period,
                points=[],
                overall_growth=0,
                best_period="",
                worst_period=""
            )
        
        points = []
        previous_value = None
        growth_rates = []
        
        for item in data:
            period_str = item.get("period", "")
            value = item.get(metric, 0)
            
            growth_rate = None
            if previous_value is not None and previous_value > 0:
                growth_rate = ((value - previous_value) / previous_value) * 100
                growth_rates.append(growth_rate)
            
            points.append(TrendPoint(
                period=period_str,
                value=value,
                growth_rate=growth_rate
            ))
            
            previous_value = value
        
        overall_growth = sum(growth_rates) / len(growth_rates) if growth_rates else 0
        
        # Find best and worst periods
        best_period = max(points, key=lambda x: x.growth_rate if x.growth_rate else -float('inf'))
        worst_period = min(points, key=lambda x: x.growth_rate if x.growth_rate else float('inf'))
        
        return TrendReport(
            metric=metric,
            dimension="overall",
            granularity=period,
            points=points,
            overall_growth=round(overall_growth, 1),
            best_period=best_period.period if best_period.growth_rate else "",
            worst_period=worst_period.period if worst_period.growth_rate else ""
        )
    
    def warehouse_trend(self, warehouse_kpis: List[Dict], warehouse_name: str, 
                        metric: str = "revenue", periods: List[str] = None) -> TrendReport:
        """Analyze specific warehouse trend over time"""
        # Filter data for specific warehouse
        warehouse_data = [k for k in warehouse_kpis if k.get("warehouse_name") == warehouse_name]
        
        return self._generate_trend(warehouse_data, metric, "monthly")
    
    def dealer_trend(self, dealer_kpis: List[Dict], dealer_name: str,
                     metric: str = "revenue", periods: List[str] = None) -> TrendReport:
        """Analyze specific dealer trend over time"""
        dealer_data = [k for k in dealer_kpis if k.get("dealer_name") == dealer_name]
        
        return self._generate_trend(dealer_data, metric, "monthly")
    
    # ==========================================================
    # MODULE 4: EXECUTIVE DASHBOARD ENGINE
    # ==========================================================
    
    def build_executive_dashboard(self, overall_kpi: Dict, dealer_kpis: List[Dict],
                                   warehouse_kpis: List[Dict], product_kpis: List[Dict],
                                   city_kpis: List[Dict], division_kpis: List[Dict]) -> ExecutiveDashboard:
        """
        Build complete executive dashboard
        
        Required KPIs from overall_kpi:
        - total_revenue, total_units, total_dns
        - total_delivered, total_pending_delivery, total_pending_pod
        - delivery_rate, pod_rate, pgi_rate
        - avg_delivery_aging, avg_pod_aging
        - critical_deliveries, critical_pod
        """
        
        # Find top performers
        top_dealer = max(dealer_kpis, key=lambda x: x.get("revenue", 0)) if dealer_kpis else {}
        top_warehouse = max(warehouse_kpis, key=lambda x: x.get("revenue", 0)) if warehouse_kpis else {}
        top_product = max(product_kpis, key=lambda x: x.get("units", 0)) if product_kpis else {}
        top_city = max(city_kpis, key=lambda x: x.get("revenue", 0)) if city_kpis else {}
        top_division = max(division_kpis, key=lambda x: x.get("revenue", 0)) if division_kpis else {}
        
        # Identify red risk locations
        red_risk_locations = []
        for wh in warehouse_kpis:
            if wh.get("risk_level") == "RED":
                red_risk_locations.append(wh.get("warehouse_name", "Unknown"))
        
        for city in city_kpis:
            if city.get("risk_level") == "RED":
                red_risk_locations.append(city.get("city_name", "Unknown"))
        
        # Calculate health score
        health_score = self._calculate_health_score(overall_kpi)
        
        # Determine risk summary
        risk_summary = self._get_risk_summary(overall_kpi)
        
        return ExecutiveDashboard(
            total_revenue=overall_kpi.get("total_revenue", 0),
            total_units=overall_kpi.get("total_units", 0),
            total_dns=overall_kpi.get("total_dns", 0),
            total_delivered=overall_kpi.get("total_delivered", 0),
            total_pending_delivery=overall_kpi.get("total_pending_delivery", 0),
            total_pending_pod=overall_kpi.get("total_pending_pod", 0),
            delivery_rate=overall_kpi.get("delivery_rate", 0),
            pod_rate=overall_kpi.get("pod_rate", 0),
            pgi_rate=overall_kpi.get("pgi_rate", 0),
            avg_delivery_aging=overall_kpi.get("avg_delivery_aging", 0),
            avg_pod_aging=overall_kpi.get("avg_pod_aging", 0),
            top_dealer=top_dealer.get("dealer_name", "N/A"),
            top_warehouse=top_warehouse.get("warehouse_name", "N/A"),
            top_product=top_product.get("product_name", top_product.get("product_code", "N/A")),
            top_city=top_city.get("city_name", "N/A"),
            top_division=top_division.get("division_name", "N/A"),
            critical_deliveries=overall_kpi.get("critical_deliveries", 0),
            critical_pod=overall_kpi.get("critical_pod", 0),
            red_risk_locations=red_risk_locations[:5],
            risk_summary=risk_summary,
            health_score=health_score
        )
    
    def _calculate_health_score(self, overall_kpi: Dict) -> float:
        """Calculate overall business health score (0-100)"""
        score = 0
        
        # Delivery rate (30%)
        delivery_rate = overall_kpi.get("delivery_rate", 0)
        score += delivery_rate * 0.3
        
        # POD rate (30%)
        pod_rate = overall_kpi.get("pod_rate", 0)
        score += pod_rate * 0.3
        
        # PGI rate (20%)
        pgi_rate = overall_kpi.get("pgi_rate", 0)
        score += pgi_rate * 0.2
        
        # Aging performance (20%)
        avg_delivery_aging = overall_kpi.get("avg_delivery_aging", 10)
        aging_score = max(0, 100 - (avg_delivery_aging * 5))
        score += aging_score * 0.2
        
        return round(score, 1)
    
    def _get_risk_summary(self, overall_kpi: Dict) -> str:
        """Generate risk summary text"""
        critical_deliveries = overall_kpi.get("critical_deliveries", 0)
        critical_pod = overall_kpi.get("critical_pod", 0)
        avg_aging = overall_kpi.get("avg_delivery_aging", 0)
        
        if critical_deliveries > 50 or critical_pod > 100 or avg_aging > 10:
            return "🔴 HIGH RISK - Immediate attention required"
        elif critical_deliveries > 20 or critical_pod > 50 or avg_aging > 7:
            return "🟡 MEDIUM RISK - Monitor closely"
        elif critical_deliveries > 5 or critical_pod > 20:
            return "🟠 ELEVATED RISK - Review processes"
        else:
            return "🟢 LOW RISK - Operating normally"
    
    # ==========================================================
    # MODULE 5: CONTROL TOWER ENGINE
    # ==========================================================
    
    def critical_delivery_report(self, warehouse_kpis: List[Dict], 
                                  dealer_kpis: List[Dict],
                                  threshold_days: int = 15) -> ControlTowerReport:
        """Generate report of critical deliveries"""
        alerts = []
        
        # Check warehouses
        for wh in warehouse_kpis:
            pending_delivery = wh.get("pending_delivery", 0)
            aging = wh.get("avg_delivery_aging", 0)
            
            if aging > threshold_days:
                severity = self._get_severity(aging, "delivery")
                alerts.append(ControlTowerAlert(
                    severity=severity,
                    category="delivery",
                    entity_type="warehouse",
                    entity_name=wh.get("warehouse_name", "Unknown"),
                    message=f"Delivery aging at {aging:.1f} days with {pending_delivery} pending",
                    metric_value=aging,
                    threshold=threshold_days,
                    days_at_risk=int(aging - threshold_days)
                ))
        
        # Check dealers
        for dealer in dealer_kpis:
            pending_delivery = dealer.get("pending_delivery", 0)
            if pending_delivery > 10:
                alerts.append(ControlTowerAlert(
                    severity="YELLOW",
                    category="delivery",
                    entity_type="dealer",
                    entity_name=dealer.get("dealer_name", "Unknown"),
                    message=f"{pending_delivery} pending deliveries",
                    metric_value=pending_delivery,
                    threshold=10
                ))
        
        # Find worst performers
        worst_warehouse = max(warehouse_kpis, key=lambda x: x.get("avg_delivery_aging", 0)) if warehouse_kpis else {}
        worst_dealer = max(dealer_kpis, key=lambda x: x.get("pending_delivery", 0)) if dealer_kpis else {}
        worst_city = max([w for w in warehouse_kpis], key=lambda x: x.get("avg_delivery_aging", 0)) if warehouse_kpis else {}
        
        # Calculate risk summary
        risk_summary = self._calculate_risk_summary(alerts)
        
        return ControlTowerReport(
            alerts=alerts,
            risk_summary=risk_summary,
            worst_warehouse=worst_warehouse.get("warehouse_name", "N/A"),
            worst_dealer=worst_dealer.get("dealer_name", "N/A"),
            worst_city=worst_city.get("city_name", "N/A") if worst_city else "N/A",
            oldest_pending_delivery_days=int(max([a.days_at_risk or 0 for a in alerts]) if alerts else 0),
            oldest_pending_pod_days=0,
            generated_at=datetime.now().isoformat()
        )
    
    def critical_pod_report(self, warehouse_kpis: List[Dict],
                            dealer_kpis: List[Dict],
                            threshold_days: int = 15) -> ControlTowerReport:
        """Generate report of critical POD issues"""
        alerts = []
        
        for wh in warehouse_kpis:
            pending_pod = wh.get("pending_pod", 0)
            pod_aging = wh.get("avg_pod_aging", 0)
            
            if pod_aging > threshold_days:
                severity = self._get_severity(pod_aging, "pod")
                alerts.append(ControlTowerAlert(
                    severity=severity,
                    category="pod",
                    entity_type="warehouse",
                    entity_name=wh.get("warehouse_name", "Unknown"),
                    message=f"POD aging at {pod_aging:.1f} days with {pending_pod} pending",
                    metric_value=pod_aging,
                    threshold=threshold_days,
                    days_at_risk=int(pod_aging - threshold_days)
                ))
        
        for dealer in dealer_kpis:
            pending_pod = dealer.get("pending_pod", 0)
            if pending_pod > 20:
                alerts.append(ControlTowerAlert(
                    severity="YELLOW",
                    category="pod",
                    entity_type="dealer",
                    entity_name=dealer.get("dealer_name", "Unknown"),
                    message=f"{pending_pod} pending PODs",
                    metric_value=pending_pod,
                    threshold=20
                ))
        
        worst_warehouse = max(warehouse_kpis, key=lambda x: x.get("avg_pod_aging", 0)) if warehouse_kpis else {}
        worst_dealer = max(dealer_kpis, key=lambda x: x.get("pending_pod", 0)) if dealer_kpis else {}
        
        risk_summary = self._calculate_risk_summary(alerts)
        
        return ControlTowerReport(
            alerts=alerts,
            risk_summary=risk_summary,
            worst_warehouse=worst_warehouse.get("warehouse_name", "N/A"),
            worst_dealer=worst_dealer.get("dealer_name", "N/A"),
            worst_city="N/A",
            oldest_pending_delivery_days=0,
            oldest_pending_pod_days=int(max([a.days_at_risk or 0 for a in alerts]) if alerts else 0),
            generated_at=datetime.now().isoformat()
        )
    
    def warehouse_risk_report(self, warehouse_kpis: List[Dict]) -> List[ControlTowerAlert]:
        """Generate risk report for warehouses"""
        alerts = []
        
        for wh in warehouse_kpis:
            risk_score = 0
            reasons = []
            
            delivery_aging = wh.get("avg_delivery_aging", 0)
            pod_aging = wh.get("avg_pod_aging", 0)
            pending_delivery = wh.get("pending_delivery", 0)
            pending_pod = wh.get("pending_pod", 0)
            
            if delivery_aging > 15:
                risk_score += 3
                reasons.append(f"High delivery aging: {delivery_aging:.1f} days")
            elif delivery_aging > 7:
                risk_score += 1
            
            if pod_aging > 15:
                risk_score += 3
                reasons.append(f"High POD aging: {pod_aging:.1f} days")
            elif pod_aging > 7:
                risk_score += 1
            
            if pending_delivery > 100:
                risk_score += 2
            elif pending_delivery > 50:
                risk_score += 1
            
            if pending_pod > 200:
                risk_score += 2
            elif pending_pod > 100:
                risk_score += 1
            
            if risk_score >= 6:
                severity = "RED"
            elif risk_score >= 3:
                severity = "ORANGE"
            elif risk_score >= 1:
                severity = "YELLOW"
            else:
                severity = "GREEN"
            
            if severity != "GREEN":
                alerts.append(ControlTowerAlert(
                    severity=severity,
                    category="warehouse_risk",
                    entity_type="warehouse",
                    entity_name=wh.get("warehouse_name", "Unknown"),
                    message=" | ".join(reasons),
                    metric_value=risk_score,
                    threshold=3
                ))
        
        return alerts
    
    def dealer_risk_report(self, dealer_kpis: List[Dict]) -> List[ControlTowerAlert]:
        """Generate risk report for dealers"""
        alerts = []
        
        for dealer in dealer_kpis:
            risk_score = 0
            reasons = []
            
            pending_pod = dealer.get("pending_pod", 0)
            pending_delivery = dealer.get("pending_delivery", 0)
            delivery_rate = dealer.get("delivery_rate", 100)
            
            if pending_pod > 50:
                risk_score += 3
                reasons.append(f"High pending POD: {pending_pod}")
            elif pending_pod > 20:
                risk_score += 1
            
            if pending_delivery > 20:
                risk_score += 2
            elif pending_delivery > 5:
                risk_score += 1
            
            if delivery_rate < 70:
                risk_score += 3
                reasons.append(f"Low delivery rate: {delivery_rate:.1f}%")
            elif delivery_rate < 85:
                risk_score += 1
            
            if risk_score >= 5:
                severity = "RED"
            elif risk_score >= 3:
                severity = "ORANGE"
            elif risk_score >= 1:
                severity = "YELLOW"
            else:
                severity = "GREEN"
            
            if severity != "GREEN":
                alerts.append(ControlTowerAlert(
                    severity=severity,
                    category="dealer_risk",
                    entity_type="dealer",
                    entity_name=dealer.get("dealer_name", "Unknown"),
                    message=" | ".join(reasons),
                    metric_value=risk_score,
                    threshold=3
                ))
        
        return alerts
    
    def _get_severity(self, value: float, metric_type: str) -> str:
        """Get severity based on value and metric type"""
        if metric_type == "delivery":
            if value >= 31:
                return "RED"
            elif value >= 16:
                return "ORANGE"
            elif value >= 8:
                return "YELLOW"
            else:
                return "GREEN"
        else:  # pod
            if value >= 31:
                return "RED"
            elif value >= 16:
                return "ORANGE"
            elif value >= 8:
                return "YELLOW"
            else:
                return "GREEN"
    
    def _calculate_risk_summary(self, alerts: List[ControlTowerAlert]) -> Dict[str, int]:
        """Calculate summary of risks by severity"""
        summary = {"RED": 0, "ORANGE": 0, "YELLOW": 0, "GREEN": 0}
        for alert in alerts:
            summary[alert.severity] = summary.get(alert.severity, 0) + 1
        return summary
    
    # ==========================================================
    # MODULE 6: ROOT CAUSE PREPARATION ENGINE
    # ==========================================================
    
    def prepare_root_cause_data(self, warehouse_kpis: List[Dict], 
                                 dealer_kpis: List[Dict],
                                 product_kpis: List[Dict],
                                 city_kpis: List[Dict],
                                 focus: str = "delivery") -> Dict[str, Any]:
        """
        Prepare data for GROQ root cause analysis
        
        Args:
            focus: delivery, pod, revenue
        """
        if focus == "delivery":
            return self._prepare_delivery_root_cause(warehouse_kpis, dealer_kpis, product_kpis, city_kpis)
        elif focus == "pod":
            return self._prepare_pod_root_cause(warehouse_kpis, dealer_kpis, product_kpis, city_kpis)
        else:
            return self._prepare_revenue_root_cause(dealer_kpis, product_kpis, city_kpis)
    
    def _prepare_delivery_root_cause(self, warehouse_kpis, dealer_kpis, product_kpis, city_kpis) -> Dict:
        """Find drivers of delivery delays"""
        
        # Find top delayed warehouses
        delayed_warehouses = sorted(warehouse_kpis, 
                                    key=lambda x: x.get("avg_delivery_aging", 0), 
                                    reverse=True)[:3]
        
        # Find dealers with most pending deliveries
        pending_dealers = sorted(dealer_kpis,
                                 key=lambda x: x.get("pending_delivery", 0),
                                 reverse=True)[:3]
        
        # Find products with most issues
        delayed_products = sorted(product_kpis,
                                  key=lambda x: x.get("delivery_aging", 0),
                                  reverse=True)[:3] if product_kpis else []
        
        # Find cities with delivery problems
        delayed_cities = sorted(city_kpis,
                                key=lambda x: x.get("avg_delivery_aging", 0),
                                reverse=True)[:3] if city_kpis else []
        
        return {
            "focus": "delivery",
            "top_causes": {
                "warehouses": [
                    {"name": w.get("warehouse_name"), "avg_aging": w.get("avg_delivery_aging", 0),
                     "pending": w.get("pending_delivery", 0)}
                    for w in delayed_warehouses
                ],
                "dealers": [
                    {"name": d.get("dealer_name"), "pending": d.get("pending_delivery", 0)}
                    for d in pending_dealers
                ],
                "products": [
                    {"name": p.get("product_name", p.get("product_code")), "aging": p.get("delivery_aging", 0)}
                    for p in delayed_products
                ],
                "cities": [
                    {"name": c.get("city_name"), "avg_aging": c.get("avg_delivery_aging", 0)}
                    for c in delayed_cities
                ]
            },
            "summary": {
                "total_delayed_warehouses": len([w for w in warehouse_kpis if w.get("avg_delivery_aging", 0) > 7]),
                "total_dealers_with_pending": len([d for d in dealer_kpis if d.get("pending_delivery", 0) > 5]),
                "primary_driver": delayed_warehouses[0].get("warehouse_name") if delayed_warehouses else "Unknown"
            }
        }
    
    def _prepare_pod_root_cause(self, warehouse_kpis, dealer_kpis, product_kpis, city_kpis) -> Dict:
        """Find drivers of POD delays"""
        
        delayed_warehouses = sorted(warehouse_kpis,
                                    key=lambda x: x.get("avg_pod_aging", 0),
                                    reverse=True)[:3]
        
        pending_dealers = sorted(dealer_kpis,
                                 key=lambda x: x.get("pending_pod", 0),
                                 reverse=True)[:3]
        
        return {
            "focus": "pod",
            "top_causes": {
                "warehouses": [
                    {"name": w.get("warehouse_name"), "avg_aging": w.get("avg_pod_aging", 0),
                     "pending": w.get("pending_pod", 0)}
                    for w in delayed_warehouses
                ],
                "dealers": [
                    {"name": d.get("dealer_name"), "pending": d.get("pending_pod", 0)}
                    for d in pending_dealers
                ]
            },
            "summary": {
                "total_warehouses_with_high_pod_aging": len([w for w in warehouse_kpis if w.get("avg_pod_aging", 0) > 7]),
                "primary_driver": delayed_warehouses[0].get("warehouse_name") if delayed_warehouses else "Unknown"
            }
        }
    
    def _prepare_revenue_root_cause(self, dealer_kpis, product_kpis, city_kpis) -> Dict:
        """Find drivers of revenue issues"""
        
        low_revenue_dealers = sorted(dealer_kpis,
                                     key=lambda x: x.get("revenue", 0))[:3] if dealer_kpis else []
        
        low_revenue_products = sorted(product_kpis,
                                      key=lambda x: x.get("revenue", 0))[:3] if product_kpis else []
        
        low_revenue_cities = sorted(city_kpis,
                                    key=lambda x: x.get("revenue", 0))[:3] if city_kpis else []
        
        return {
            "focus": "revenue",
            "top_causes": {
                "dealers": [
                    {"name": d.get("dealer_name"), "revenue": d.get("revenue", 0)}
                    for d in low_revenue_dealers
                ],
                "products": [
                    {"name": p.get("product_name", p.get("product_code")), "revenue": p.get("revenue", 0)}
                    for p in low_revenue_products
                ],
                "cities": [
                    {"name": c.get("city_name"), "revenue": c.get("revenue", 0)}
                    for c in low_revenue_cities
                ]
            }
        }
    
    def find_delay_drivers(self, warehouse_kpis: List[Dict]) -> Dict[str, Any]:
        """Identify main drivers of delivery delays"""
        if not warehouse_kpis:
            return {"top_cause": "Unknown", "affected_dns": 0}
        
        # Find warehouse with highest delivery aging
        worst_warehouse = max(warehouse_kpis, key=lambda x: x.get("avg_delivery_aging", 0))
        
        return {
            "top_cause": worst_warehouse.get("warehouse_name", "Unknown"),
            "metric": "delivery_aging",
            "value": worst_warehouse.get("avg_delivery_aging", 0),
            "affected_dns": worst_warehouse.get("total_dns", 0),
            "pending_count": worst_warehouse.get("pending_delivery", 0)
        }
    
    def find_pod_drivers(self, warehouse_kpis: List[Dict]) -> Dict[str, Any]:
        """Identify main drivers of POD delays"""
        if not warehouse_kpis:
            return {"top_cause": "Unknown", "affected_dns": 0}
        
        worst_warehouse = max(warehouse_kpis, key=lambda x: x.get("avg_pod_aging", 0))
        
        return {
            "top_cause": worst_warehouse.get("warehouse_name", "Unknown"),
            "metric": "pod_aging",
            "value": worst_warehouse.get("avg_pod_aging", 0),
            "affected_dns": worst_warehouse.get("total_dns", 0),
            "pending_pod": worst_warehouse.get("pending_pod", 0)
        }
    
    # ==========================================================
    # MODULE 7: PERFORMANCE SCORE ENGINE
    # ==========================================================
    
    def calculate_dealer_score(self, dealer_kpi: Dict) -> PerformanceScore:
        """Calculate performance score for a dealer (0-100)"""
        score = 0
        
        # Delivery rate (40%)
        delivery_rate = dealer_kpi.get("delivery_rate", 0)
        score += delivery_rate * 0.4
        
        # POD rate (40%)
        pod_rate = dealer_kpi.get("pod_rate", 0)
        score += pod_rate * 0.4
        
        # Revenue growth (20%) - simplified for now
        revenue = dealer_kpi.get("revenue", 0)
        revenue_score = min(100, revenue / 1000000)  # 1M = 100 points
        score += revenue_score * 0.2
        
        score = round(score, 1)
        
        # Determine grade
        if score >= 90:
            grade = "A"
        elif score >= 80:
            grade = "B"
        elif score >= 70:
            grade = "C"
        elif score >= 60:
            grade = "D"
        else:
            grade = "F"
        
        return PerformanceScore(
            entity_name=dealer_kpi.get("dealer_name", "Unknown"),
            entity_type="dealer",
            score=score,
            grade=grade,
            breakdown={
                "delivery_rate": delivery_rate,
                "pod_rate": pod_rate,
                "revenue_score": revenue_score
            }
        )
    
    def calculate_warehouse_score(self, warehouse_kpi: Dict) -> PerformanceScore:
        """Calculate performance score for a warehouse (0-100)"""
        score = 0
        
        # Delivery rate (40%)
        delivery_rate = warehouse_kpi.get("delivery_rate", 100)
        score += delivery_rate * 0.4
        
        # POD rate (40%)
        pod_rate = warehouse_kpi.get("pod_rate", 100)
        score += pod_rate * 0.4
        
        # Aging performance (20%)
        delivery_aging = warehouse_kpi.get("avg_delivery_aging", 0)
        aging_score = max(0, 100 - (delivery_aging * 5))
        score += aging_score * 0.2
        
        score = round(score, 1)
        
        if score >= 90:
            grade = "A"
        elif score >= 80:
            grade = "B"
        elif score >= 70:
            grade = "C"
        elif score >= 60:
            grade = "D"
        else:
            grade = "F"
        
        return PerformanceScore(
            entity_name=warehouse_kpi.get("warehouse_name", "Unknown"),
            entity_type="warehouse",
            score=score,
            grade=grade,
            breakdown={
                "delivery_rate": delivery_rate,
                "pod_rate": pod_rate,
                "aging_score": aging_score
            }
        )
    
    # ==========================================================
    # MODULE 8: SLA ANALYTICS ENGINE
    # ==========================================================
    
    def delivery_sla_analysis(self, warehouse_kpis: List[Dict]) -> List[SLAAnalysis]:
        """Analyze delivery SLA buckets for warehouses"""
        results = []
        
        for wh in warehouse_kpis:
            results.append(SLAAnalysis(
                entity_name=wh.get("warehouse_name", "Unknown"),
                same_day=wh.get("same_day_delivery", 0),
                one_day=wh.get("one_day_delivery", 0),
                two_day=wh.get("two_day_delivery", 0),
                three_day=wh.get("three_day_delivery", 0),
                four_day=wh.get("four_day_delivery", 0),
                five_plus=wh.get("five_plus_delivery", 0),
                average_days=wh.get("avg_delivery_aging", 0)
            ))
        
        return results
    
    def pod_sla_analysis(self, warehouse_kpis: List[Dict]) -> List[SLAAnalysis]:
        """Analyze POD SLA buckets for warehouses"""
        results = []
        
        for wh in warehouse_kpis:
            results.append(SLAAnalysis(
                entity_name=wh.get("warehouse_name", "Unknown"),
                same_day=wh.get("same_day_pod", 0),
                one_day=wh.get("one_day_pod", 0),
                two_day=wh.get("two_day_pod", 0),
                three_day=wh.get("three_day_pod", 0),
                four_day=wh.get("four_day_pod", 0),
                five_plus=wh.get("five_plus_pod", 0),
                average_days=wh.get("avg_pod_aging", 0)
            ))
        
        return results
    
    # ==========================================================
    # MODULE 9: FORECAST PREPARATION ENGINE
    # ==========================================================
    
    def forecast_revenue_input(self, historical_revenue: List[float]) -> Dict[str, Any]:
        """Prepare revenue data for forecasting"""
        if not historical_revenue:
            return {"error": "No historical data"}
        
        return {
            "historical_data": historical_revenue,
            "data_points": len(historical_revenue),
            "average": sum(historical_revenue) / len(historical_revenue),
            "trend": "increasing" if historical_revenue[-1] > historical_revenue[0] else "decreasing",
            "volatility": max(historical_revenue) - min(historical_revenue)
        }
    
    # ==========================================================
    # MODULE 10: ALERT ENGINE
    # ==========================================================
    
    def generate_delivery_alerts(self, warehouse_kpis: List[Dict]) -> List[ControlTowerAlert]:
        """Generate proactive delivery alerts"""
        alerts = []
        
        for wh in warehouse_kpis:
            pending = wh.get("pending_delivery", 0)
            aging = wh.get("avg_delivery_aging", 0)
            
            if pending > 100:
                alerts.append(ControlTowerAlert(
                    severity="RED",
                    category="delivery",
                    entity_type="warehouse",
                    entity_name=wh.get("warehouse_name", "Unknown"),
                    message=f"Critical: {pending} pending deliveries",
                    metric_value=pending,
                    threshold=100
                ))
            elif aging > 15:
                alerts.append(ControlTowerAlert(
                    severity="ORANGE",
                    category="delivery",
                    entity_type="warehouse",
                    entity_name=wh.get("warehouse_name", "Unknown"),
                    message=f"High delivery aging: {aging:.1f} days",
                    metric_value=aging,
                    threshold=15
                ))
        
        return alerts
    
    def generate_pod_alerts(self, warehouse_kpis: List[Dict]) -> List[ControlTowerAlert]:
        """Generate proactive POD alerts"""
        alerts = []
        
        for wh in warehouse_kpis:
            pending = wh.get("pending_pod", 0)
            aging = wh.get("avg_pod_aging", 0)
            
            if pending > 200:
                alerts.append(ControlTowerAlert(
                    severity="RED",
                    category="pod",
                    entity_type="warehouse",
                    entity_name=wh.get("warehouse_name", "Unknown"),
                    message=f"Critical: {pending} pending PODs",
                    metric_value=pending,
                    threshold=200
                ))
            elif aging > 15:
                alerts.append(ControlTowerAlert(
                    severity="ORANGE",
                    category="pod",
                    entity_type="warehouse",
                    entity_name=wh.get("warehouse_name", "Unknown"),
                    message=f"High POD aging: {aging:.1f} days",
                    metric_value=aging,
                    threshold=15
                ))
        
        return alerts


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_analytics_service = None

def get_analytics_service() -> AnalyticsService:
    """Get singleton instance of AnalyticsService"""
    global _analytics_service
    if _analytics_service is None:
        _analytics_service = AnalyticsService()
    return _analytics_service


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 60)
logger.info("Analytics Service - Business Intelligence & Control Tower")
logger.info("=" * 60)
logger.info("")
logger.info("   MODULES LOADED:")
logger.info("   ✅ Ranking Engine")
logger.info("   ✅ Comparison Engine")
logger.info("   ✅ Trend Engine")
logger.info("   ✅ Executive Dashboard Engine")
logger.info("   ✅ Control Tower Engine")
logger.info("   ✅ Root Cause Preparation Engine")
logger.info("   ✅ Performance Score Engine")
logger.info("   ✅ SLA Analytics Engine")
logger.info("   ✅ Forecast Preparation Engine")
logger.info("   ✅ Alert Engine")
logger.info("")
logger.info("   STATUS: ✅ READY")
logger.info("=" * 60)
