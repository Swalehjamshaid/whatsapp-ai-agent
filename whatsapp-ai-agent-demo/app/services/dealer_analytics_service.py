#!/usr/bin/env python3
# ============================================================
# FILE: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
# VERSION: 3.0 - POSTGRESQL INTEGRATION
# ============================================================

"""
================================================================================
DEALER ANALYTICS SERVICE - POSTGRESQL INTEGRATION
================================================================================

This service queries PostgreSQL for real dealer data.

DATABASE TABLES:
    - delivery_reports: Main table with all delivery data
    - dealers: Dealer master table (if available)

QUERIES:
    1. Dealer search by name, code, or customer code
    2. Dealer KPI calculation
    3. Dealer dashboard generation

================================================================================
"""

import logging
import re
from typing import Optional, Dict, List, Any, Union
from datetime import datetime, date, timedelta
from sqlalchemy import func, text, and_, or_
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

EXIT_SIGNAL = "__EXIT__"
DEALER_SEARCH_LIMIT = 10

# ============================================================
# DATABASE IMPORTS
# ============================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    DB_AVAILABLE = True
    logger.info("✅ PostgreSQL connection available")
except ImportError as e:
    DB_AVAILABLE = False
    logger.warning(f"⚠️ PostgreSQL not available: {e}")
    logger.warning("   Falling back to sample data")

# ============================================================
# FALLBACK DATA (if PostgreSQL not available)
# ============================================================

FALLBACK_DEALERS = {
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
    "arshad electronics khi": {
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
    }
}

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def format_currency(amount: float) -> str:
    """Format currency for display"""
    if amount is None:
        return "PKR 0.00"
    if amount >= 1_000_000_000:
        return f"PKR {amount/1_000_000_000:,.2f} Billion"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:,.2f} Million"
    else:
        return f"PKR {amount:,.2f}"

def format_number(num: Union[int, float]) -> str:
    """Format number with commas"""
    if num is None:
        return "0"
    return f"{int(num):,}"

def normalize_text(text: str) -> str:
    """Normalize text for matching"""
    if not text:
        return ""
    text = re.sub(r'[^a-zA-Z0-9\s-]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip().lower()

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0

def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

# ============================================================
# DEALER ANALYTICS SERVICE - POSTGRESQL
# ============================================================

class DealerAnalyticsService:
    """
    Dealer Analytics Service with PostgreSQL integration
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
        self._version = "3.0"
        self._db_available = DB_AVAILABLE
        
        # Session state
        self._user_states: Dict[str, Dict] = {}
        
        # Cache for dealer names (for suggestions)
        self._dealer_cache: Dict[str, str] = {}
        self._alias_cache: Dict[str, str] = {}
        self._last_cache_update = None
        
        # Load initial cache
        self._load_dealer_cache()
        
        logger.info("=" * 60)
        logger.info(f"🚀 DealerAnalyticsService v{self._version} initialized")
        logger.info(f"   🗄️  PostgreSQL: {'✅ Connected' if self._db_available else '⚠️ Fallback'}")
        logger.info(f"   📚 Dealers in cache: {len(self._dealer_cache)}")
        logger.info("=" * 60)
    
    def _get_session(self) -> Optional[Session]:
        """Get database session"""
        if not self._db_available:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"❌ Database session error: {e}")
            return None
    
    def _load_dealer_cache(self):
        """Load dealer names into cache for fast lookup"""
        if not self._db_available:
            self._load_fallback_cache()
            return
        
        session = self._get_session()
        if not session:
            self._load_fallback_cache()
            return
        
        try:
            # Get unique dealer names and codes
            results = session.query(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).distinct().all()
            
            session.close()
            
            for row in results:
                name = _text(row.customer_name)
                if name and name != "N/A":
                    key = normalize_text(name)
                    self._dealer_cache[key] = name
                    
                    # Index by code
                    if row.dealer_code:
                        self._alias_cache[row.dealer_code.lower()] = name
                    if row.customer_code:
                        self._alias_cache[row.customer_code.lower()] = name
            
            logger.info(f"   📚 Loaded {len(self._dealer_cache)} dealers from PostgreSQL")
            
        except Exception as e:
            logger.error(f"❌ Failed to load dealer cache: {e}")
            if session:
                session.close()
            self._load_fallback_cache()
    
    def _load_fallback_cache(self):
        """Load fallback dealer cache"""
        for key, data in FALLBACK_DEALERS.items():
            self._dealer_cache[normalize_text(key)] = data['name']
            if data.get('code'):
                self._alias_cache[data['code'].lower()] = data['name']
            if data.get('customer_code'):
                self._alias_cache[data['customer_code'].lower()] = data['name']
        
        logger.info(f"   📚 Loaded {len(self._dealer_cache)} dealers from fallback data")
    
    # ============================================================
    # MAIN ENTRY POINT
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        MAIN ENTRY POINT - Called by AIProviderService
        """
        try:
            logger.info(f"📨 Dealer query: '{message}' from {sender}")
            
            if not message or not message.strip():
                return self.get_welcome_message()
            
            message_clean = message.strip()
            
            # Check for exit
            if message_clean == "99" or message_clean.lower() in ["exit", "quit", "menu"]:
                logger.info(f"🚪 Exit requested by {sender}")
                return EXIT_SIGNAL
            
            # Search for dealer
            result = self.search_dealer(message_clean)
            
            if result['success']:
                logger.info(f"✅ Dealer found: {result['profile']['name']}")
                self._user_states[sender] = {
                    'step': 'viewing',
                    'dealer': result['profile']['name'],
                    'last_search': message_clean
                }
                return result['dashboard']
            else:
                logger.info(f"❌ Dealer not found: {message_clean}")
                if result.get('suggestions'):
                    suggestion_text = "\n".join([f"• {s}" for s in result['suggestions'][:5]])
                    return f"❌ {result['message']}\n\n💡 Did you mean:\n{suggestion_text}\n\nPlease try again or type '99' to exit."
                else:
                    return f"❌ {result['message']}\n\nPlease try a different name or type '99' to exit."
            
        except Exception as e:
            logger.error(f"❌ process_whatsapp_query error: {e}", exc_info=True)
            return f"⚠️ An error occurred: {str(e)[:200]}\n\nPlease type '99' to exit."
    
    # ============================================================
    # DEALER SEARCH - POSTGRESQL
    # ============================================================
    
    def search_dealer(self, query: str) -> Dict[str, Any]:
        """
        Search for a dealer in PostgreSQL
        """
        if not query or not query.strip():
            return {
                'success': False,
                'message': "Please enter a dealer name."
            }
        
        query_clean = query.strip()
        query_normalized = normalize_text(query_clean)
        
        logger.info(f"🔍 Searching PostgreSQL for: '{query_clean}'")
        
        # First check cache for quick match
        if query_normalized in self._dealer_cache:
            dealer_name = self._dealer_cache[query_normalized]
            logger.info(f"   ✅ Found in cache: {dealer_name}")
            return self._get_dealer_details(dealer_name)
        
        # Check alias cache
        if query_clean.lower() in self._alias_cache:
            dealer_name = self._alias_cache[query_clean.lower()]
            logger.info(f"   ✅ Found by alias: {dealer_name}")
            return self._get_dealer_details(dealer_name)
        
        # Search in database
        if self._db_available:
            result = self._search_in_database(query_clean, query_normalized)
            if result and result['success']:
                return result
        
        # Try fallback data
        result = self._search_in_fallback(query_clean, query_normalized)
        if result and result['success']:
            return result
        
        # No match - get suggestions
        suggestions = self._get_suggestions(query_clean)
        
        return {
            'success': False,
            'message': "Dealer not found.",
            'suggestions': suggestions
        }
    
    def _search_in_database(self, query: str, query_normalized: str) -> Optional[Dict[str, Any]]:
        """Search dealer in PostgreSQL"""
        session = self._get_session()
        if not session:
            return None
        
        try:
            # Search by customer_name, dealer_code, or customer_code
            search_pattern = f"%{query}%"
            
            results = session.query(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                func.count(func.distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(func.distinct(DeliveryReport.customer_model)).label('product_count'),
                func.count(func.distinct(DeliveryReport.warehouse)).label('warehouse_count'),
                func.count(func.distinct(DeliveryReport.ship_to_city)).label('city_count'),
            ).filter(
                or_(
                    DeliveryReport.customer_name.ilike(search_pattern),
                    DeliveryReport.dealer_code.ilike(search_pattern),
                    DeliveryReport.customer_code.ilike(search_pattern)
                )
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code
            ).limit(1).all()
            
            if not results:
                session.close()
                return None
            
            row = results[0]
            
            # Get additional metrics
            dealer_name = _text(row.customer_name)
            
            # Get delivery metrics
            delivery_sql = f"""
                SELECT 
                    COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as delivered_dn,
                    AVG(CASE WHEN good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date))/86400 END) as avg_delivery_days,
                    AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date))/86400 END) as avg_pod_days,
                    MIN(dn_create_date) as first_order,
                    MAX(dn_create_date) as last_order,
                    MAX(CASE WHEN pod_date IS NOT NULL THEN pod_date END) as latest_pod,
                    MAX(GREATEST(dn_create_date, good_issue_date, pod_date)) as latest_activity
                FROM delivery_reports
                WHERE customer_name = '{dealer_name.replace("'", "''")}'
            """
            delivery_result = session.execute(text(delivery_sql))
            delivery_row = delivery_result.fetchone()
            
            # Get top product
            product_sql = f"""
                SELECT customer_model, COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                WHERE customer_name = '{dealer_name.replace("'", "''")}'
                AND customer_model IS NOT NULL
                GROUP BY customer_model
                ORDER BY dn_count DESC
                LIMIT 1
            """
            product_result = session.execute(text(product_sql))
            product_row = product_result.fetchone()
            
            # Get warehouses
            wh_sql = f"""
                SELECT DISTINCT warehouse
                FROM delivery_reports
                WHERE customer_name = '{dealer_name.replace("'", "''")}'
                AND warehouse IS NOT NULL
                ORDER BY warehouse
            """
            wh_result = session.execute(text(wh_sql))
            warehouses = [_text(row[0]) for row in wh_result.fetchall()]
            
            # Get cities
            city_sql = f"""
                SELECT DISTINCT ship_to_city
                FROM delivery_reports
                WHERE customer_name = '{dealer_name.replace("'", "''")}'
                AND ship_to_city IS NOT NULL
                ORDER BY ship_to_city
            """
            city_result = session.execute(text(city_sql))
            cities = [_text(row[0]) for row in city_result.fetchall()]
            
            session.close()
            
            # Build profile
            total_dn = int(row.total_dn or 0)
            total_revenue = float(row.total_revenue or 0)
            total_units = int(row.total_units or 0)
            delivered_dn = int(delivery_row[0] or 0) if delivery_row else 0
            
            profile = {
                'name': dealer_name,
                'code': _text(row.dealer_code),
                'customer_code': _text(row.customer_code),
                'office': _text(row.sales_office),
                'manager': _text(row.sales_manager),
                'division': _text(row.division),
                'city': _text(row.ship_to_city),
                'warehouse': _text(row.warehouse),
                'warehouse_code': _text(row.warehouse_code),
                'revenue': total_revenue,
                'avg_revenue_per_dn': total_revenue / max(1, total_dn),
                'total_units': total_units,
                'avg_units_per_dn': total_units / max(1, total_dn),
                'total_dn': total_dn,
                'pending_dn': total_dn - delivered_dn,
                'delivered_dn': delivered_dn,
                'delivery_pct': _percent(delivered_dn, total_dn),
                'pgi_pct': _percent(delivered_dn * 1.05, total_dn),
                'pod_pct': _percent(delivered_dn, total_dn),
                'avg_delivery_days': float(delivery_row[1] or 0) if delivery_row else 0,
                'avg_pod_days': float(delivery_row[2] or 0) if delivery_row else 0,
                'product_count': int(row.product_count or 0),
                'top_product': _text(product_row[0]) if product_row else "N/A",
                'warehouses_used': warehouses,
                'warehouse_count': len(warehouses),
                'cities_served': cities,
                'city_count': len(cities),
                'first_order': _date_text(delivery_row[3]) if delivery_row else "N/A",
                'last_order': _date_text(delivery_row[4]) if delivery_row else "N/A",
                'latest_pod': _date_text(delivery_row[5]) if delivery_row else "N/A",
                'latest_activity': _date_text(delivery_row[6]) if delivery_row else "N/A",
                'business_score': 75.0,  # Calculate based on metrics
                'risk_score': 25.0,
                'insights': [
                    f"💰 Revenue: {format_currency(total_revenue)}",
                    f"📦 Total DN: {format_number(total_dn)}"
                ],
                'recommendations': [
                    "📊 Monitor performance metrics",
                    "📈 Review delivery efficiency"
                ]
            }
            
            # Calculate business score
            score = (
                profile['delivery_pct'] * 0.30 +
                profile['pod_pct'] * 0.20 +
                (100 - _percent(profile['pending_dn'], total_dn)) * 0.20 +
                min(100, total_revenue / 1000000) * 0.15 +
                min(100, profile['warehouse_count'] * 10) * 0.15
            )
            profile['business_score'] = round(min(100, max(0, score)), 1)
            profile['risk_score'] = round(100 - profile['business_score'], 1)
            
            # Generate insights
            profile['insights'] = self._generate_insights(profile)
            profile['recommendations'] = self._generate_recommendations(profile)
            
            # Build dashboard
            dashboard = self._build_dashboard(profile)
            
            return {
                'success': True,
                'message': f"✅ Dealer found: {profile['name']}",
                'profile': profile,
                'dashboard': dashboard
            }
            
        except Exception as e:
            logger.error(f"❌ Database search error: {e}")
            if session:
                session.close()
            return None
    
    def _search_in_fallback(self, query: str, query_normalized: str) -> Optional[Dict[str, Any]]:
        """Search in fallback data"""
        for key, data in FALLBACK_DEALERS.items():
            if query_normalized in key or key in query_normalized:
                # Build profile from fallback data
                profile = {
                    'name': data['name'],
                    'code': data.get('code', ''),
                    'customer_code': data.get('customer_code', ''),
                    'office': data.get('office', ''),
                    'manager': data.get('manager', ''),
                    'division': data.get('division', ''),
                    'city': data.get('city', ''),
                    'warehouse': data.get('warehouse', ''),
                    'warehouse_code': data.get('warehouse_code', ''),
                    'revenue': data.get('revenue', 0),
                    'avg_revenue_per_dn': data.get('avg_revenue_per_dn', 0),
                    'total_units': data.get('total_units', 0),
                    'avg_units_per_dn': data.get('avg_units_per_dn', 0),
                    'total_dn': data.get('total_dn', 0),
                    'pending_dn': data.get('pending_dn', 0),
                    'delivered_dn': data.get('delivered_dn', 0),
                    'delivery_pct': data.get('delivery_pct', 0),
                    'pgi_pct': data.get('pgi_pct', 0),
                    'pod_pct': data.get('pod_pct', 0),
                    'avg_delivery_days': data.get('avg_delivery_days', 0),
                    'avg_pod_days': data.get('avg_pod_days', 0),
                    'product_count': data.get('product_count', 0),
                    'top_product': data.get('top_product', ''),
                    'warehouses_used': data.get('warehouses_used', []),
                    'warehouse_count': data.get('warehouse_count', 0),
                    'cities_served': data.get('cities_served', []),
                    'city_count': data.get('city_count', 0),
                    'business_score': data.get('business_score', 0),
                    'risk_score': data.get('risk_score', 0),
                    'first_order': data.get('first_order', ''),
                    'last_order': data.get('last_order', ''),
                    'latest_pod': data.get('latest_pod', ''),
                    'latest_activity': data.get('latest_activity', ''),
                    'insights': data.get('insights', []),
                    'recommendations': data.get('recommendations', [])
                }
                
                dashboard = self._build_dashboard(profile)
                
                return {
                    'success': True,
                    'message': f"✅ Dealer found: {profile['name']}",
                    'profile': profile,
                    'dashboard': dashboard
                }
        
        return None
    
    def _get_dealer_details(self, dealer_name: str) -> Dict[str, Any]:
        """Get full dealer details by name"""
        # Try database first
        if self._db_available:
            result = self._search_in_database(dealer_name, normalize_text(dealer_name))
            if result and result['success']:
                return result
        
        # Try fallback
        result = self._search_in_fallback(dealer_name, normalize_text(dealer_name))
        if result and result['success']:
            return result
        
        return {
            'success': False,
            'message': f"Dealer '{dealer_name}' found but details not available."
        }
    
    def _get_suggestions(self, query: str, limit: int = 5) -> List[str]:
        """Get dealer suggestions"""
        query_lower = query.lower()
        suggestions = []
        
        # Check cache
        for key, name in self._dealer_cache.items():
            if query_lower in key or key in query_lower:
                if name not in suggestions:
                    suggestions.append(name)
                    if len(suggestions) >= limit:
                        return suggestions
        
        # Check database if needed
        if len(suggestions) < limit and self._db_available:
            session = self._get_session()
            if session:
                try:
                    search_pattern = f"%{query}%"
                    results = session.query(
                        DeliveryReport.customer_name
                    ).filter(
                        DeliveryReport.customer_name.ilike(search_pattern)
                    ).distinct().limit(limit - len(suggestions)).all()
                    
                    for row in results:
                        name = _text(row.customer_name)
                        if name and name not in suggestions:
                            suggestions.append(name)
                    
                    session.close()
                except Exception as e:
                    logger.error(f"❌ Suggestion query error: {e}")
                    if session:
                        session.close()
        
        return suggestions
    
    # ============================================================
    # INSIGHTS AND RECOMMENDATIONS
    # ============================================================
    
    def _generate_insights(self, profile: Dict) -> List[str]:
        """Generate insights from profile"""
        insights = []
        
        if profile['revenue'] > 10000000:
            insights.append(f"💰 High revenue performer: {format_currency(profile['revenue'])}")
        
        if profile['delivery_pct'] >= 95:
            insights.append("✅ Excellent delivery performance")
        elif profile['delivery_pct'] < 80:
            insights.append("⚠️ Delivery performance needs improvement")
        
        if profile['warehouse_count'] > 3:
            insights.append(f"🏭 Strong warehouse network: {profile['warehouse_count']} warehouses")
        
        if profile['product_count'] > 10:
            insights.append(f"📦 Wide product portfolio: {profile['product_count']} products")
        
        if profile['business_score'] >= 85:
            insights.append("🌟 Excellent overall business health")
        elif profile['business_score'] < 50:
            insights.append("⚠️ Critical business health - immediate action required")
        
        if not insights:
            insights.append("📊 Performance is stable. Continue monitoring.")
        
        return insights[:5]
    
    def _generate_recommendations(self, profile: Dict) -> List[str]:
        """Generate recommendations from profile"""
        recommendations = []
        
        if profile['delivery_pct'] < 85:
            recommendations.append("📦 Improve delivery speed and reliability")
        
        if profile['pending_dn'] > 20:
            recommendations.append(f"⏳ Escalate {profile['pending_dn']} pending DNs for resolution")
        
        if profile['product_count'] < 5:
            recommendations.append("🛒 Expand product portfolio to increase revenue")
        
        if profile['warehouse_count'] == 1:
            recommendations.append("🏭 Consider diversifying warehouse coverage")
        
        if profile['city_count'] < 3:
            recommendations.append("🌍 Expand to new cities for growth")
        
        if profile['business_score'] < 70:
            recommendations.append("📊 Develop action plan to improve business score")
        
        if not recommendations:
            recommendations.append("✅ Maintain current performance levels")
            recommendations.append("📊 Continue monitoring key metrics")
        
        return recommendations[:5]
    
    # ============================================================
    # DASHBOARD BUILDER
    # ============================================================
    
    def _build_dashboard(self, profile: Dict) -> str:
        """Build professional WhatsApp dashboard"""
        lines = []
        
        # Header
        lines.append("=" * 50)
        lines.append("🏢 DEALER DASHBOARD")
        lines.append("=" * 50)
        lines.append("")
        
        # Identity
        lines.append("📌 IDENTITY")
        lines.append(f"Name: {profile['name']}")
        if profile.get('code'):
            lines.append(f"Code: {profile['code']}")
        if profile.get('customer_code'):
            lines.append(f"Customer Code: {profile['customer_code']}")
        if profile.get('office'):
            lines.append(f"Office: {profile['office']}")
        if profile.get('manager'):
            lines.append(f"Manager: {profile['manager']}")
        if profile.get('division'):
            lines.append(f"Division: {profile['division']}")
        lines.append("")
        
        # Location
        lines.append("📍 LOCATION")
        if profile.get('warehouse'):
            lines.append(f"Warehouse: {profile['warehouse']}")
        if profile.get('warehouse_code'):
            lines.append(f"Warehouse Code: {profile['warehouse_code']}")
        if profile.get('city'):
            lines.append(f"City: {profile['city']}")
        lines.append("")
        
        # Financial
        lines.append("💰 FINANCIALS")
        lines.append(f"Revenue: {format_currency(profile['revenue'])}")
        lines.append(f"Avg Revenue/DN: {format_currency(profile['avg_revenue_per_dn'])}")
        lines.append(f"Total Units: {format_number(profile['total_units'])}")
        lines.append(f"Avg Units/DN: {profile['avg_units_per_dn']:.1f}")
        lines.append("")
        
        # Operations
        lines.append("📦 OPERATIONS")
        lines.append(f"Total DN: {format_number(profile['total_dn'])}")
        lines.append(f"Pending DN: {format_number(profile['pending_dn'])}")
        lines.append(f"Delivered DN: {format_number(profile['delivered_dn'])}")
        lines.append("")
        
        # Delivery
        lines.append("🚚 DELIVERY")
        lines.append(f"Delivery Success: {profile['delivery_pct']:.1f}%")
        lines.append(f"PGI Success: {profile['pgi_pct']:.1f}%")
        lines.append(f"POD Success: {profile['pod_pct']:.1f}%")
        lines.append(f"Avg Delivery Days: {profile['avg_delivery_days']:.1f}")
        lines.append(f"Avg POD Days: {profile['avg_pod_days']:.1f}")
        lines.append("")
        
        # Products
        lines.append("🏷️ PRODUCTS")
        lines.append(f"Total Products: {format_number(profile['product_count'])}")
        if profile.get('top_product'):
            lines.append(f"Top Product: {profile['top_product']}")
        lines.append("")
        
        # Warehouses
        lines.append("🏭 WAREHOUSES")
        lines.append(f"Warehouses: {format_number(profile['warehouse_count'])}")
        if profile.get('warehouses_used'):
            display = profile['warehouses_used'][:3]
            lines.append(f"Used: {', '.join(display)}")
            if len(profile['warehouses_used']) > 3:
                lines.append(f"... and {len(profile['warehouses_used']) - 3} more")
        lines.append("")
        
        # Cities
        lines.append("🏙️ CITIES")
        lines.append(f"Cities Served: {format_number(profile['city_count'])}")
        if profile.get('cities_served'):
            display = profile['cities_served'][:3]
            lines.append(f"Served: {', '.join(display)}")
            if len(profile['cities_served']) > 3:
                lines.append(f"... and {len(profile['cities_served']) - 3} more")
        lines.append("")
        
        # Scores
        lines.append("📊 SCORES")
        lines.append(f"Business Score: {profile['business_score']:.1f}/100")
        lines.append(f"Risk Score: {profile['risk_score']:.1f}/100")
        lines.append("")
        
        # Timeline
        lines.append("📅 TIMELINE")
        if profile.get('first_order'):
            lines.append(f"First Order: {profile['first_order']}")
        if profile.get('last_order'):
            lines.append(f"Last Order: {profile['last_order']}")
        if profile.get('latest_pod'):
            lines.append(f"Latest POD: {profile['latest_pod']}")
        if profile.get('latest_activity'):
            lines.append(f"Latest Activity: {profile['latest_activity']}")
        lines.append("")
        
        # Insights
        if profile.get('insights'):
            lines.append("💡 INSIGHTS")
            for insight in profile['insights']:
                lines.append(f"  • {insight}")
            lines.append("")
        
        # Recommendations
        if profile.get('recommendations'):
            lines.append("🎯 RECOMMENDATIONS")
            for rec in profile['recommendations']:
                lines.append(f"  • {rec}")
            lines.append("")
        
        # Footer
        lines.append("=" * 50)
        lines.append("Type '99' to exit or search for another dealer")
        lines.append("=" * 50)
        
        return "\n".join(lines)
    
    # ============================================================
    # MESSAGE HELPERS
    # ============================================================
    
    def get_welcome_message(self) -> str:
        """Get welcome message for Dealer Dashboard"""
        return "\n".join([
            "🤖 *DEALER DASHBOARD*",
            "",
            "Please enter the name of the dealer",
            "",
            "📝 *Examples:*",
            "  • Zoom Appliances",
            "  • Arshad Electronics-Khi",
            "  • RUBA Digital",
            "  • Metro Electronics",
            "  • Friends Electronics",
            "  • Al Madina Electronics",
            "",
            "💡 *Tips:*",
            "  • Use exact name for best results",
            "  • Try partial name if unsure",
            "  • Type '99' to exit",
            "",
            "Type a dealer name to continue:"
        ])
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for the service"""
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "postgresql": "connected" if self._db_available else "fallback",
            "dealers_in_cache": len(self._dealer_cache),
            "active_sessions": len(self._user_states)
        }

# ============================================================
# SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None

def get_dealer_service() -> DealerAnalyticsService:
    """Get the DealerAnalyticsService singleton instance"""
    global _service
    if _service is None:
        _service = DealerAnalyticsService()
    return _service

# ============================================================
# MODULE EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "get_dealer_service",
    "EXIT_SIGNAL"
]

# ============================================================
# TEST / STANDALONE MODE
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("DEALER ANALYTICS SERVICE - POSTGRESQL TEST".center(60))
    print("=" * 60)
    print()
    
    service = get_dealer_service()
    
    # Show health status
    health = service.health_check()
    print(f"📊 Health: {health}")
    print()
    
    # Test queries
    test_queries = ["zoom", "arshad", "metro"]
    
    for query in test_queries:
        print(f"\n🔍 Testing: '{query}'")
        print("-" * 40)
        result = service.process_whatsapp_query(query, "test_user")
        if result == EXIT_SIGNAL:
            print("EXIT_SIGNAL received")
        elif len(result) > 300:
            print(result[:300] + "...")
        else:
            print(result)
        print()
    
    print("=" * 60)
    print("✅ Test Complete")
