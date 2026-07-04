#!/usr/bin/env python3
# ============================================================
# FILE: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
# VERSION: 2.0 - WHATSAPP DEALER ANALYTICS SERVICE
# ============================================================

"""
================================================================================
WHATSAPP DEALER ANALYTICS SERVICE
================================================================================

This is a COMPLETE, INDEPENDENT file for WhatsApp AI Agent Demo.

STARTUP BEHAVIOR:
    1. Initializes the service
    2. Loads dealer data
    3. DISPLAYS: "Please enter the name of the dealer"
    4. Waits for user input
    5. Searches and displays dashboard
    6. Returns to prompt for next search
    7. Type '99' to exit

================================================================================
FILE PATH: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
================================================================================
"""

import re
import sys
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any

# ============================================================
# DEALER DATA
# ============================================================

DEALER_DATABASE = {
    "zoom appliances": {
        "name": "Zoom Appliances",
        "code": "ZA-001",
        "customer_code": "CUST-1001",
        "office": "Karachi",
        "manager": "Ahmed Khan",
        "division": "Electronics",
        "warehouse": "Karachi Warehouse",
        "warehouse_code": "WH-KHI-01",
        "city": "Karachi",
        "revenue": 15678900.50,
        "avg_revenue_per_dn": 63995.51,
        "total_units": 1234,
        "avg_units_per_dn": 5.0,
        "total_dn": 245,
        "pending_dn": 37,
        "delivered_dn": 208,
        "delivery_pct": 84.9,
        "pgi_pct": 89.1,
        "pod_pct": 84.9,
        "avg_delivery_days": 2.5,
        "avg_pod_days": 1.2,
        "product_count": 8,
        "top_product": "Electronics",
        "warehouses_used": ["Karachi Warehouse"],
        "warehouse_count": 1,
        "cities_served": ["Karachi"],
        "city_count": 1,
        "business_score": 62.0,
        "risk_score": 38.0,
        "first_order": "15-Jan-2025",
        "last_order": "01-Jul-2026",
        "latest_pod": "28-Jun-2026",
        "latest_activity": "01-Jul-2026",
        "insights": [
            "💰 High revenue performer: PKR 15,678,900.50",
            "⚠️ Delivery performance needs improvement"
        ],
        "recommendations": [
            "📦 Improve delivery speed and reliability",
            "🏭 Consider diversifying warehouse coverage",
            "🌍 Expand to new cities for growth"
        ]
    },
    "arshad electronics": {
        "name": "Arshad Electronics-Khi",
        "code": "AE-002",
        "customer_code": "CUST-1002",
        "office": "Karachi",
        "manager": "Saima Arshad",
        "division": "Electronics",
        "warehouse": "Karachi Warehouse",
        "warehouse_code": "WH-KHI-01",
        "city": "Karachi",
        "revenue": 9876543.75,
        "avg_revenue_per_dn": 52256.85,
        "total_units": 876,
        "avg_units_per_dn": 4.6,
        "total_dn": 189,
        "pending_dn": 28,
        "delivered_dn": 161,
        "delivery_pct": 85.2,
        "pgi_pct": 89.4,
        "pod_pct": 85.2,
        "avg_delivery_days": 2.3,
        "avg_pod_days": 1.1,
        "product_count": 6,
        "top_product": "TV",
        "warehouses_used": ["Karachi Warehouse"],
        "warehouse_count": 1,
        "cities_served": ["Karachi"],
        "city_count": 1,
        "business_score": 58.5,
        "risk_score": 41.5,
        "first_order": "20-Jan-2025",
        "last_order": "28-Jun-2026",
        "latest_pod": "25-Jun-2026",
        "latest_activity": "28-Jun-2026",
        "insights": [
            "📊 Steady business performance",
            "✅ Good delivery track record"
        ],
        "recommendations": [
            "📈 Expand product portfolio",
            "🌍 Consider new city expansion"
        ]
    },
    "ruha digital": {
        "name": "RUBA Digital",
        "code": "RD-003",
        "customer_code": "CUST-1003",
        "office": "Lahore",
        "manager": "Usman Ali",
        "division": "Digital",
        "warehouse": "Lahore Warehouse",
        "warehouse_code": "WH-LHR-01",
        "city": "Lahore",
        "revenue": 22345678.00,
        "avg_revenue_per_dn": 71620.76,
        "total_units": 2100,
        "avg_units_per_dn": 6.7,
        "total_dn": 312,
        "pending_dn": 47,
        "delivered_dn": 265,
        "delivery_pct": 84.9,
        "pgi_pct": 89.1,
        "pod_pct": 84.9,
        "avg_delivery_days": 2.5,
        "avg_pod_days": 1.2,
        "product_count": 12,
        "top_product": "Digital Devices",
        "warehouses_used": ["Lahore Warehouse"],
        "warehouse_count": 1,
        "cities_served": ["Lahore"],
        "city_count": 1,
        "business_score": 68.0,
        "risk_score": 32.0,
        "first_order": "10-Jan-2025",
        "last_order": "30-Jun-2026",
        "latest_pod": "27-Jun-2026",
        "latest_activity": "30-Jun-2026",
        "insights": [
            "💰 High revenue performer: PKR 22,345,678.00",
            "📦 Wide product portfolio: 12 products"
        ],
        "recommendations": [
            "📦 Improve delivery speed",
            "🏭 Consider warehouse diversification"
        ]
    },
    "metro electronics": {
        "name": "Metro Electronics",
        "code": "ME-004",
        "customer_code": "CUST-1004",
        "office": "Islamabad",
        "manager": "Fatima Malik",
        "division": "Electronics",
        "warehouse": "Islamabad Warehouse",
        "warehouse_code": "WH-ISB-01",
        "city": "Islamabad",
        "revenue": 8765432.25,
        "avg_revenue_per_dn": 52487.62,
        "total_units": 723,
        "avg_units_per_dn": 4.3,
        "total_dn": 167,
        "pending_dn": 25,
        "delivered_dn": 142,
        "delivery_pct": 85.0,
        "pgi_pct": 89.2,
        "pod_pct": 85.0,
        "avg_delivery_days": 2.4,
        "avg_pod_days": 1.1,
        "product_count": 5,
        "top_product": "Appliances",
        "warehouses_used": ["Islamabad Warehouse"],
        "warehouse_count": 1,
        "cities_served": ["Islamabad"],
        "city_count": 1,
        "business_score": 55.0,
        "risk_score": 45.0,
        "first_order": "25-Jan-2025",
        "last_order": "29-Jun-2026",
        "latest_pod": "26-Jun-2026",
        "latest_activity": "29-Jun-2026",
        "insights": [
            "📊 Stable business performance",
            "✅ Good delivery track record"
        ],
        "recommendations": [
            "📈 Expand product portfolio",
            "🌍 Consider new city expansion"
        ]
    },
    "friends electronics": {
        "name": "Friends Electronics",
        "code": "FE-005",
        "customer_code": "CUST-1005",
        "office": "Karachi",
        "manager": "Bilal Ahmed",
        "division": "Electronics",
        "warehouse": "Karachi Warehouse",
        "warehouse_code": "WH-KHI-02",
        "city": "Karachi",
        "revenue": 4567890.00,
        "avg_revenue_per_dn": 46611.12,
        "total_units": 456,
        "avg_units_per_dn": 4.7,
        "total_dn": 98,
        "pending_dn": 15,
        "delivered_dn": 83,
        "delivery_pct": 84.7,
        "pgi_pct": 88.9,
        "pod_pct": 84.7,
        "avg_delivery_days": 2.6,
        "avg_pod_days": 1.3,
        "product_count": 4,
        "top_product": "Mobile",
        "warehouses_used": ["Karachi Warehouse"],
        "warehouse_count": 1,
        "cities_served": ["Karachi"],
        "city_count": 1,
        "business_score": 50.0,
        "risk_score": 50.0,
        "first_order": "05-Feb-2025",
        "last_order": "27-Jun-2026",
        "latest_pod": "24-Jun-2026",
        "latest_activity": "27-Jun-2026",
        "insights": [
            "📊 Growing business",
            "✅ Good customer base"
        ],
        "recommendations": [
            "📦 Improve delivery speed",
            "🛒 Expand product portfolio"
        ]
    },
    "al madina electronics": {
        "name": "Al Madina Electronics",
        "code": "AME-006",
        "customer_code": "CUST-1006",
        "office": "Lahore",
        "manager": "Muhammad Hassan",
        "division": "Electronics",
        "warehouse": "Lahore Warehouse",
        "warehouse_code": "WH-LHR-02",
        "city": "Lahore",
        "revenue": 5678901.50,
        "avg_revenue_per_dn": 36403.21,
        "total_units": 634,
        "avg_units_per_dn": 4.1,
        "total_dn": 156,
        "pending_dn": 23,
        "delivered_dn": 133,
        "delivery_pct": 85.3,
        "pgi_pct": 89.5,
        "pod_pct": 85.3,
        "avg_delivery_days": 2.2,
        "avg_pod_days": 1.0,
        "product_count": 7,
        "top_product": "Electronics",
        "warehouses_used": ["Lahore Warehouse"],
        "warehouse_count": 1,
        "cities_served": ["Lahore"],
        "city_count": 1,
        "business_score": 60.0,
        "risk_score": 40.0,
        "first_order": "15-Feb-2025",
        "last_order": "26-Jun-2026",
        "latest_pod": "23-Jun-2026",
        "latest_activity": "26-Jun-2026",
        "insights": [
            "📊 Steady growth",
            "✅ Good delivery performance"
        ],
        "recommendations": [
            "🌍 Expand to new cities",
            "📈 Increase product variety"
        ]
    }
}

# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class DealerMatch:
    dealer_name: str
    dealer_code: str
    customer_code: str
    score: float
    match_type: str
    confidence: float

@dataclass
class DealerProfile:
    name: str = ""
    code: str = ""
    customer_code: str = ""
    office: str = ""
    manager: str = ""
    division: str = ""
    warehouse: str = ""
    warehouse_code: str = ""
    city: str = ""
    revenue: float = 0.0
    avg_revenue_per_dn: float = 0.0
    total_units: int = 0
    avg_units_per_dn: float = 0.0
    total_dn: int = 0
    pending_dn: int = 0
    delivered_dn: int = 0
    delivery_pct: float = 0.0
    pgi_pct: float = 0.0
    pod_pct: float = 0.0
    avg_delivery_days: float = 0.0
    avg_pod_days: float = 0.0
    product_count: int = 0
    top_product: str = ""
    warehouses_used: List[str] = field(default_factory=list)
    warehouse_count: int = 0
    cities_served: List[str] = field(default_factory=list)
    city_count: int = 0
    business_score: float = 0.0
    risk_score: float = 0.0
    first_order: str = ""
    last_order: str = ""
    latest_pod: str = ""
    latest_activity: str = ""
    insights: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

@dataclass
class SearchResult:
    success: bool
    message: str = ""
    profile: Optional[DealerProfile] = None
    dashboard: str = ""
    matches: List[DealerMatch] = field(default_factory=list)

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def format_currency(amount: float) -> str:
    if amount >= 1_000_000_000:
        return f"PKR {amount/1_000_000_000:,.2f} Billion"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:,.2f} Million"
    else:
        return f"PKR {amount:,.2f}"

def format_number(num: int) -> str:
    return f"{num:,}"

def normalize_text(text: str) -> str:
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip().lower()

# ============================================================
# DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """
    WhatsApp Dealer Analytics Service
    
    Starts with: "Please enter the name of the dealer"
    """
    
    _instance: Optional["DealerAnalyticsService"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self._initialized = True
        self._service_name = "dealer_analytics"
        self._version = "2.0"
        
        # Load dealer data
        self._dealer_cache = {}
        self._load_dealers()
        
        print("\n" + "=" * 50)
        print("DEALER ANALYTICS SERVICE")
        print("=" * 50)
        print()
    
    def _load_dealers(self):
        """Load dealers from database"""
        for key, data in DEALER_DATABASE.items():
            self._dealer_cache[key] = data
        print(f"✅ Loaded {len(self._dealer_cache)} dealers")
        print()
    
    def search_dealer(self, query: str) -> SearchResult:
        """Search for a dealer"""
        if not query or not query.strip():
            return SearchResult(
                success=False,
                message="Please enter a dealer name."
            )
        
        query_clean = query.strip()
        
        # Stage 1: Exact Match
        result = self._exact_match(query_clean)
        if result:
            return self._build_result(result)
        
        # Stage 2: Case Insensitive
        result = self._case_insensitive_match(query_clean)
        if result:
            return self._build_result(result)
        
        # Stage 3: Partial Match
        result = self._partial_match(query_clean)
        if result:
            return self._build_result(result)
        
        # Stage 4: Word Match
        result = self._word_match(query_clean)
        if result:
            return self._build_result(result)
        
        # Stage 5: Dealer Code
        result = self._code_match(query_clean)
        if result:
            return self._build_result(result)
        
        # No match - get suggestions
        suggestions = self._get_suggestions(query_clean)
        
        if suggestions:
            suggestion_text = "\n".join([f"  • {s.dealer_name}" for s in suggestions[:3]])
            return SearchResult(
                success=False,
                message=f"Dealer not found. Did you mean:\n{suggestion_text}"
            )
        
        return SearchResult(
            success=False,
            message="Dealer not found. Please try a different name."
        )
    
    def _exact_match(self, query: str):
        query_lower = query.lower()
        for key, data in self._dealer_cache.items():
            if key == query_lower:
                return DealerMatch(
                    dealer_name=data['name'],
                    dealer_code=data['code'],
                    customer_code=data['customer_code'],
                    score=100.0,
                    match_type="exact",
                    confidence=1.0
                )
        return None
    
    def _case_insensitive_match(self, query: str):
        query_lower = query.lower()
        for key, data in self._dealer_cache.items():
            if key == query_lower:
                return DealerMatch(
                    dealer_name=data['name'],
                    dealer_code=data['code'],
                    customer_code=data['customer_code'],
                    score=99.0,
                    match_type="case_insensitive",
                    confidence=0.99
                )
        return None
    
    def _partial_match(self, query: str):
        query_lower = query.lower()
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            if query_lower in key:
                score = len(query) / len(key)
                if score > best_score:
                    best_score = score
                    best_match = data
        
        if best_match and best_score > 0.4:
            return DealerMatch(
                dealer_name=best_match['name'],
                dealer_code=best_match['code'],
                customer_code=best_match['customer_code'],
                score=best_score * 100,
                match_type="partial",
                confidence=best_score
            )
        return None
    
    def _word_match(self, query: str):
        query_words = set(query.lower().split())
        if len(query_words) < 2:
            return None
        
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            key_words = set(key.split())
            common_words = query_words & key_words
            if common_words:
                score = len(common_words) / len(query_words)
                if score > best_score:
                    best_score = score
                    best_match = data
        
        if best_match and best_score > 0.5:
            return DealerMatch(
                dealer_name=best_match['name'],
                dealer_code=best_match['code'],
                customer_code=best_match['customer_code'],
                score=best_score * 100,
                match_type="word",
                confidence=best_score
            )
        return None
    
    def _code_match(self, query: str):
        query_clean = query.strip().upper()
        for key, data in self._dealer_cache.items():
            if data['code'] == query_clean or data['customer_code'] == query_clean:
                return DealerMatch(
                    dealer_name=data['name'],
                    dealer_code=data['code'],
                    customer_code=data['customer_code'],
                    score=99.0,
                    match_type="code",
                    confidence=0.99
                )
        return None
    
    def _get_suggestions(self, query: str, limit: int = 3) -> List[DealerMatch]:
        query_lower = query.lower()
        suggestions = []
        
        for key, data in self._dealer_cache.items():
            if query_lower in key or key in query_lower:
                suggestions.append(
                    DealerMatch(
                        dealer_name=data['name'],
                        dealer_code=data['code'],
                        customer_code=data['customer_code'],
                        score=70.0,
                        match_type="suggestion",
                        confidence=0.70
                    )
                )
                if len(suggestions) >= limit:
                    break
        
        return suggestions
    
    def _build_result(self, match: DealerMatch) -> SearchResult:
        """Build search result with dashboard"""
        # Get full profile data
        dealer_data = None
        for key, data in self._dealer_cache.items():
            if data['name'] == match.dealer_name:
                dealer_data = data
                break
        
        if not dealer_data:
            return SearchResult(
                success=False,
                message=f"Dealer '{match.dealer_name}' found but data not available."
            )
        
        # Create profile
        profile = DealerProfile(
            name=dealer_data['name'],
            code=dealer_data['code'],
            customer_code=dealer_data['customer_code'],
            office=dealer_data['office'],
            manager=dealer_data['manager'],
            division=dealer_data['division'],
            warehouse=dealer_data['warehouse'],
            warehouse_code=dealer_data['warehouse_code'],
            city=dealer_data['city'],
            revenue=dealer_data['revenue'],
            avg_revenue_per_dn=dealer_data['avg_revenue_per_dn'],
            total_units=dealer_data['total_units'],
            avg_units_per_dn=dealer_data['avg_units_per_dn'],
            total_dn=dealer_data['total_dn'],
            pending_dn=dealer_data['pending_dn'],
            delivered_dn=dealer_data['delivered_dn'],
            delivery_pct=dealer_data['delivery_pct'],
            pgi_pct=dealer_data['pgi_pct'],
            pod_pct=dealer_data['pod_pct'],
            avg_delivery_days=dealer_data['avg_delivery_days'],
            avg_pod_days=dealer_data['avg_pod_days'],
            product_count=dealer_data['product_count'],
            top_product=dealer_data['top_product'],
            warehouses_used=dealer_data['warehouses_used'],
            warehouse_count=dealer_data['warehouse_count'],
            cities_served=dealer_data['cities_served'],
            city_count=dealer_data['city_count'],
            business_score=dealer_data['business_score'],
            risk_score=dealer_data['risk_score'],
            first_order=dealer_data['first_order'],
            last_order=dealer_data['last_order'],
            latest_pod=dealer_data['latest_pod'],
            latest_activity=dealer_data['latest_activity'],
            insights=dealer_data['insights'],
            recommendations=dealer_data['recommendations']
        )
        
        # Build dashboard
        dashboard = self._build_dashboard(profile)
        
        return SearchResult(
            success=True,
            message=f"✅ Dealer found: {match.dealer_name}",
            profile=profile,
            dashboard=dashboard
        )
    
    def _build_dashboard(self, profile: DealerProfile) -> str:
        """Build professional WhatsApp dashboard"""
        lines = []
        
        # Header
        lines.append("=" * 50)
        lines.append("🏢 DEALER DASHBOARD")
        lines.append("=" * 50)
        lines.append("")
        
        # Identity
        lines.append("📌 IDENTITY")
        lines.append(f"Name: {profile.name}")
        if profile.code:
            lines.append(f"Code: {profile.code}")
        if profile.customer_code:
            lines.append(f"Customer Code: {profile.customer_code}")
        if profile.office:
            lines.append(f"Office: {profile.office}")
        if profile.manager:
            lines.append(f"Manager: {profile.manager}")
        if profile.division:
            lines.append(f"Division: {profile.division}")
        lines.append("")
        
        # Location
        lines.append("📍 LOCATION")
        if profile.warehouse:
            lines.append(f"Warehouse: {profile.warehouse}")
        if profile.warehouse_code:
            lines.append(f"Warehouse Code: {profile.warehouse_code}")
        if profile.city:
            lines.append(f"City: {profile.city}")
        lines.append("")
        
        # Financial
        lines.append("💰 FINANCIALS")
        lines.append(f"Revenue: {format_currency(profile.revenue)}")
        lines.append(f"Avg Revenue/DN: {format_currency(profile.avg_revenue_per_dn)}")
        lines.append(f"Total Units: {format_number(profile.total_units)}")
        lines.append(f"Avg Units/DN: {profile.avg_units_per_dn:.1f}")
        lines.append("")
        
        # Operations
        lines.append("📦 OPERATIONS")
        lines.append(f"Total DN: {format_number(profile.total_dn)}")
        lines.append(f"Pending DN: {format_number(profile.pending_dn)}")
        lines.append(f"Delivered DN: {format_number(profile.delivered_dn)}")
        lines.append("")
        
        # Delivery
        lines.append("🚚 DELIVERY")
        lines.append(f"Delivery Success: {profile.delivery_pct:.1f}%")
        lines.append(f"PGI Success: {profile.pgi_pct:.1f}%")
        lines.append(f"POD Success: {profile.pod_pct:.1f}%")
        lines.append(f"Avg Delivery Days: {profile.avg_delivery_days:.1f}")
        lines.append(f"Avg POD Days: {profile.avg_pod_days:.1f}")
        lines.append("")
        
        # Products
        lines.append("🏷️ PRODUCTS")
        lines.append(f"Total Products: {format_number(profile.product_count)}")
        if profile.top_product:
            lines.append(f"Top Product: {profile.top_product}")
        lines.append("")
        
        # Warehouses
        lines.append("🏭 WAREHOUSES")
        lines.append(f"Warehouses: {format_number(profile.warehouse_count)}")
        if profile.warehouses_used:
            display = profile.warehouses_used[:3]
            lines.append(f"Used: {', '.join(display)}")
            if len(profile.warehouses_used) > 3:
                lines.append(f"... and {len(profile.warehouses_used) - 3} more")
        lines.append("")
        
        # Cities
        lines.append("🏙️ CITIES")
        lines.append(f"Cities Served: {format_number(profile.city_count)}")
        if profile.cities_served:
            display = profile.cities_served[:3]
            lines.append(f"Served: {', '.join(display)}")
            if len(profile.cities_served) > 3:
                lines.append(f"... and {len(profile.cities_served) - 3} more")
        lines.append("")
        
        # Scores
        lines.append("📊 SCORES")
        lines.append(f"Business Score: {profile.business_score:.1f}/100")
        lines.append(f"Risk Score: {profile.risk_score:.1f}/100")
        lines.append("")
        
        # Timeline
        lines.append("📅 TIMELINE")
        if profile.first_order:
            lines.append(f"First Order: {profile.first_order}")
        if profile.last_order:
            lines.append(f"Last Order: {profile.last_order}")
        if profile.latest_pod:
            lines.append(f"Latest POD: {profile.latest_pod}")
        if profile.latest_activity:
            lines.append(f"Latest Activity: {profile.latest_activity}")
        lines.append("")
        
        # Insights
        if profile.insights:
            lines.append("💡 INSIGHTS")
            for insight in profile.insights:
                lines.append(f"  • {insight}")
            lines.append("")
        
        # Recommendations
        if profile.recommendations:
            lines.append("🎯 RECOMMENDATIONS")
            for rec in profile.recommendations:
                lines.append(f"  • {rec}")
            lines.append("")
        
        # Footer
        lines.append("=" * 50)
        lines.append("Type '99' to exit")
        lines.append("=" * 50)
        
        return "\n".join(lines)
    
    def get_welcome(self) -> str:
        """Get welcome message"""
        return "Please enter the name of the dealer"
    
    def get_help(self) -> str:
        """Get help message"""
        return "\n".join([
            "📝 Examples:",
            "  • Zoom Appliances",
            "  • Arshad Electronics-Khi",
            "  • RUBA Digital",
            "  • Metro Electronics",
            "  • Friends Electronics",
            "  • Al Madina Electronics",
            "",
            "💡 Tips:",
            "  • Use exact name for best results",
            "  • Try partial name if unsure",
            "  • Type '99' to exit"
        ])

# ============================================================
# SERVICE SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None

def get_dealer_analytics_service() -> DealerAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        _service = DealerAnalyticsService()
    return _service

# ============================================================
# MAIN INTERACTIVE LOOP
# ============================================================

def main():
    """Main entry point for WhatsApp AI Agent Demo"""
    print("\n" + "=" * 60)
    print("WHATSAPP AI AGENT DEMO - DEALER ANALYTICS".center(60))
    print("=" * 60)
    print()
    
    # Initialize service
    service = get_dealer_analytics_service()
    
    # Display welcome
    print(service.get_welcome())
    print()
    print(service.get_help())
    print()
    
    # Interactive loop
    while True:
        try:
            # Prompt for dealer name
            query = input("🔍 Enter Dealer Name (or '99' to exit): ").strip()
            
            # Check for exit
            if query == "99":
                print("\n👋 Goodbye!")
                break
            
            if not query:
                print("⚠️ Please enter a dealer name.\n")
                continue
            
            # Search
            print("\n⏳ Searching...")
            result = service.search_dealer(query)
            
            if result.success:
                print("\n" + result.dashboard)
                print()
            else:
                print(f"\n❌ {result.message}")
                print()
        
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n⚠️ An error occurred: {e}")
            print("Please try again.\n")

# ============================================================
# MODULE EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "DealerProfile",
    "DealerMatch",
    "SearchResult",
    "get_dealer_analytics_service",
    "main",
]

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
