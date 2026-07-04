#!/usr/bin/env python3
# ============================================================
# FILE: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
# VERSION: 3.2 - FIXED DEALER DETECTION
# ============================================================

"""
================================================================================
DEALER ANALYTICS SERVICE - FIXED DEALER DETECTION
================================================================================

FIXES:
1. Better filtering of actual dealers (not order descriptions)
2. Uses dealer_code and customer_code as primary identifiers
3. Filters out non-dealer records
4. Better suggestions
5. Handles partial matches properly

================================================================================
"""

import logging
import re
from typing import Optional, Dict, List, Any, Union
from datetime import datetime, date, timedelta
from sqlalchemy import func, text, and_, or_, desc, asc
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
# DEALER ANALYTICS SERVICE - FIXED
# ============================================================

class DealerAnalyticsService:
    """
    Dealer Analytics Service with improved dealer detection
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
        self._version = "3.2"
        self._db_available = DB_AVAILABLE
        
        # Session state
        self._user_states: Dict[str, Dict] = {}
        
        # Dealer cache
        self._dealer_cache: Dict[str, Dict] = {}
        self._code_cache: Dict[str, str] = {}
        self._name_cache: Dict[str, str] = {}
        
        # Load dealers
        self._load_dealers()
        
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
    
    def _load_dealers(self):
        """Load dealers from PostgreSQL with proper filtering"""
        if not self._db_available:
            self._load_sample_dealers()
            return
        
        session = self._get_session()
        if not session:
            self._load_sample_dealers()
            return
        
        try:
            # Get all distinct dealer records with proper filtering
            # Only include records that look like actual dealers
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
                func.count(func.distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue')
            ).filter(
                # Filter out non-dealer records
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != '',
                # Dealer code should exist and be meaningful
                DeliveryReport.dealer_code.isnot(None),
                DeliveryReport.dealer_code != '',
                # Exclude records that look like orders or descriptions
                ~DeliveryReport.customer_name.like('PK%'),  # Exclude PK orders
                ~DeliveryReport.customer_name.like('%-prepaid-%'),  # Exclude prepaid
                ~DeliveryReport.customer_name.like('%Faiq%'),  # Exclude individual names
                ~DeliveryReport.customer_name.like('%Alam%'),  # Exclude individual names
                # Dealer code should start with DEAL_ or contain ELECTRON
                or_(
                    DeliveryReport.dealer_code.like('DEAL_%'),
                    DeliveryReport.customer_name.ilike('%Electronics%'),
                    DeliveryReport.customer_name.ilike('%Digital%'),
                    DeliveryReport.customer_name.ilike('%Appliances%'),
                    DeliveryReport.dealer_code.like('D%')
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
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).all()
            
            session.close()
            
            for row in results:
                name = _text(row.customer_name)
                dealer_code = _text(row.dealer_code)
                customer_code = _text(row.customer_code)
                
                if name and name != "N/A" and len(name) > 3:
                    # Store in cache
                    key = normalize_text(name)
                    self._dealer_cache[key] = {
                        'name': name,
                        'dealer_code': dealer_code,
                        'customer_code': customer_code,
                        'office': _text(row.sales_office),
                        'manager': _text(row.sales_manager),
                        'division': _text(row.division),
                        'city': _text(row.ship_to_city),
                        'warehouse': _text(row.warehouse),
                        'warehouse_code': _text(row.warehouse_code),
                        'dn_count': int(row.dn_count or 0),
                        'total_units': int(row.total_units or 0),
                        'total_revenue': float(row.total_revenue or 0)
                    }
                    
                    # Index by codes
                    if dealer_code and dealer_code != "N/A":
                        self._code_cache[dealer_code.lower()] = name
                    if customer_code and customer_code != "N/A":
                        self._code_cache[customer_code.lower()] = name
                    
                    # Index by name parts
                    self._name_cache[key] = name
            
            logger.info(f"   ✅ Loaded {len(self._dealer_cache)} dealers from PostgreSQL")
            
        except Exception as e:
            logger.error(f"❌ Failed to load dealers: {e}")
            if session:
                session.close()
            self._load_sample_dealers()
    
    def _load_sample_dealers(self):
        """Load sample dealer data for testing"""
        sample_dealers = {
            "arshad electronics-khi": {
                "name": "Arshad Electronics-Khi",
                "dealer_code": "DEAL_ARSHAD_ELECTRON",
                "customer_code": "CUST_ARSHAD_ELECTRON",
                "office": "Karachi Office",
                "manager": "Traditional Channel",
                "division": "Washing Machine",
                "city": "Karachi",
                "warehouse": "Karachi",
                "warehouse_code": "KHI",
                "dn_count": 4,
                "total_units": 29,
                "total_revenue": 738427.00
            },
            "zoom appliances": {
                "name": "Zoom Appliances",
                "dealer_code": "DEAL_ZOOM_APP",
                "customer_code": "CUST_ZOOM_APP",
                "office": "Karachi Office",
                "manager": "Ahmed Khan",
                "division": "Electronics",
                "city": "Karachi",
                "warehouse": "Karachi",
                "warehouse_code": "KHI",
                "dn_count": 245,
                "total_units": 1234,
                "total_revenue": 15678900.50
            }
        }
        
        for key, data in sample_dealers.items():
            self._dealer_cache[key] = data
            if data.get('dealer_code'):
                self._code_cache[data['dealer_code'].lower()] = data['name']
            if data.get('customer_code'):
                self._code_cache[data['customer_code'].lower()] = data['name']
            self._name_cache[key] = data['name']
        
        logger.info(f"   📚 Loaded {len(self._dealer_cache)} sample dealers")
    
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
    # DEALER SEARCH - IMPROVED
    # ============================================================
    
    def search_dealer(self, query: str) -> Dict[str, Any]:
        """
        Search for a dealer with improved matching
        """
        if not query or not query.strip():
            return {
                'success': False,
                'message': "Please enter a dealer name."
            }
        
        query_clean = query.strip()
        query_normalized = normalize_text(query_clean)
        
        logger.info(f"🔍 Searching for: '{query_clean}'")
        
        # Try exact match in cache
        if query_normalized in self._dealer_cache:
            dealer_data = self._dealer_cache[query_normalized]
            logger.info(f"   ✅ Found in cache: {dealer_data['name']}")
            return self._get_dealer_details(dealer_data['name'])
        
        # Try code match
        if query_clean.lower() in self._code_cache:
            dealer_name = self._code_cache[query_clean.lower()]
            logger.info(f"   ✅ Found by code: {dealer_name}")
            return self._get_dealer_details(dealer_name)
        
        # Try partial match
        for key, data in self._dealer_cache.items():
            if query_normalized in key or key in query_normalized:
                logger.info(f"   ✅ Partial match: {data['name']}")
                return self._get_dealer_details(data['name'])
        
        # Try word match
        query_words = set(query_normalized.split())
        if len(query_words) >= 2:
            for key, data in self._dealer_cache.items():
                key_words = set(key.split())
                if query_words & key_words:
                    logger.info(f"   ✅ Word match: {data['name']}")
                    return self._get_dealer_details(data['name'])
        
        # Search in database if available
        if self._db_available:
            result = self._search_in_database(query_clean)
            if result and result['success']:
                return result
        
        # No match - get suggestions
        suggestions = self._get_suggestions(query_clean)
        
        return {
            'success': False,
            'message': "Dealer not found.",
            'suggestions': suggestions
        }
    
    def _search_in_database(self, query: str) -> Optional[Dict[str, Any]]:
        """Search dealer in PostgreSQL with proper filtering"""
        session = self._get_session()
        if not session:
            return None
        
        try:
            search_pattern = f"%{query}%"
            
            # Search for dealer by name, code, or customer code
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
                func.sum(DeliveryReport.dn_amount).label('total_revenue')
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != '',
                or_(
                    DeliveryReport.customer_name.ilike(search_pattern),
                    DeliveryReport.dealer_code.ilike(search_pattern),
                    DeliveryReport.customer_code.ilike(search_pattern)
                ),
                # Only include records that look like dealers
                or_(
                    DeliveryReport.dealer_code.like('DEAL_%'),
                    DeliveryReport.customer_name.ilike('%Electronics%'),
                    DeliveryReport.customer_name.ilike('%Digital%'),
                    DeliveryReport.customer_name.ilike('%Appliances%')
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
            ).limit(5).all()
            
            if not results:
                session.close()
                return None
            
            # Return first result
            row = results[0]
            dealer_name = _text(row.customer_name)
            
            session.close()
            
            return self._get_dealer_details(dealer_name)
            
        except Exception as e:
            logger.error(f"❌ Database search error: {e}")
            if session:
                session.close()
            return None
    
    def _get_dealer_details(self, dealer_name: str) -> Dict[str, Any]:
        """Get full dealer details by name"""
        session = self._get_session()
        
        try:
            # Get dealer summary
            summary = session.query(
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
                func.count(func.distinct(
                    func.case(
                        [(DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)],
                        else_=None
                    )
                )).label('delivered_dn')
            ).filter(
                DeliveryReport.customer_name == dealer_name
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
            ).first()
            
            if not summary:
                if session:
                    session.close()
                return {
                    'success': False,
                    'message': f"Dealer '{dealer_name}' found but details not available."
                }
            
            # Get additional metrics
            metrics_sql = f"""
                SELECT 
                    AVG(CASE WHEN good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date))/86400 END) as avg_delivery_days,
                    AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date))/86400 END) as avg_pod_days,
                    MIN(dn_create_date) as first_order,
                    MAX(dn_create_date) as last_order,
                    MAX(pod_date) as latest_pod
                FROM delivery_reports
                WHERE customer_name = '{dealer_name.replace("'", "''")}'
            """
            metrics_result = session.execute(text(metrics_sql))
            metrics_row = metrics_result.fetchone()
            
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
            """
            wh_result = session.execute(text(wh_sql))
            warehouses = [_text(row[0]) for row in wh_result.fetchall()]
            
            # Get cities
            city_sql = f"""
                SELECT DISTINCT ship_to_city
                FROM delivery_reports
                WHERE customer_name = '{dealer_name.replace("'", "''")}'
                AND ship_to_city IS NOT NULL
            """
            city_result = session.execute(text(city_sql))
            cities = [_text(row[0]) for row in city_result.fetchall()]
            
            session.close()
            
            # Build profile
            total_dn = int(summary.total_dn or 0)
            total_revenue = float(summary.total_revenue or 0)
            total_units = int(summary.total_units or 0)
            delivered_dn = int(summary.delivered_dn or 0)
            
            profile = {
                'name': dealer_name,
                'code': _text(summary.dealer_code),
                'customer_code': _text(summary.customer_code),
                'office': _text(summary.sales_office),
                'manager': _text(summary.sales_manager),
                'division': _text(summary.division),
                'city': _text(summary.ship_to_city),
                'warehouse': _text(summary.warehouse),
                'warehouse_code': _text(summary.warehouse_code),
                'revenue': total_revenue,
                'avg_revenue_per_dn': total_revenue / max(1, total_dn),
                'total_units': total_units,
                'avg_units_per_dn': total_units / max(1, total_dn),
                'total_dn': total_dn,
                'pending_dn': total_dn - delivered_dn,
                'delivered_dn': delivered_dn,
                'delivery_pct': _percent(delivered_dn, total_dn),
                'pgi_pct': _percent(delivered_dn, total_dn),
                'pod_pct': _percent(delivered_dn, total_dn),
                'avg_delivery_days': float(metrics_row[0] or 0) if metrics_row else 0,
                'avg_pod_days': float(metrics_row[1] or 0) if metrics_row else 0,
                'product_count': int(summary.product_count or 0),
                'top_product': _text(product_row[0]) if product_row else "N/A",
                'warehouses_used': warehouses if warehouses else ["N/A"],
                'warehouse_count': len(warehouses) or 1,
                'cities_served': cities if cities else ["N/A"],
                'city_count': len(cities) or 1,
                'first_order': _date_text(metrics_row[2]) if metrics_row else "N/A",
                'last_order': _date_text(metrics_row[3]) if metrics_row else "N/A",
                'latest_pod': _date_text(metrics_row[4]) if metrics_row else "N/A",
                'latest_activity': _date_text(metrics_row[4]) if metrics_row else "N/A",
            }
            
            # Calculate scores
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
            logger.error(f"❌ Error getting dealer details: {e}")
            if session:
                session.close()
            return {
                'success': False,
                'message': f"Error retrieving dealer details: {str(e)[:100]}"
            }
    
    def _get_suggestions(self, query: str, limit: int = 5) -> List[str]:
        """Get dealer suggestions"""
        query_lower = query.lower()
        suggestions = []
        
        # Check cache
        for key, data in self._dealer_cache.items():
            if query_lower in key or key in query_lower:
                if data['name'] not in suggestions:
                    suggestions.append(data['name'])
                    if len(suggestions) >= limit:
                        return suggestions
        
        # Check database
        if len(suggestions) < limit and self._db_available:
            session = self._get_session()
            if session:
                try:
                    search_pattern = f"%{query}%"
                    results = session.query(
                        DeliveryReport.customer_name
                    ).filter(
                        DeliveryReport.customer_name.ilike(search_pattern),
                        or_(
                            DeliveryReport.dealer_code.like('DEAL_%'),
                            DeliveryReport.customer_name.ilike('%Electronics%'),
                            DeliveryReport.customer_name.ilike('%Digital%'),
                            DeliveryReport.customer_name.ilike('%Appliances%')
                        )
                    ).distinct().limit(limit - len(suggestions)).all()
                    
                    for row in results:
                        name = _text(row.customer_name)
                        if name and name not in suggestions and len(name) > 3:
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
        elif profile['revenue'] > 1000000:
            insights.append(f"💰 Revenue: {format_currency(profile['revenue'])}")
        
        if profile['delivery_pct'] >= 95:
            insights.append("✅ Excellent delivery performance (95%+)")
        elif profile['delivery_pct'] >= 80:
            insights.append(f"✅ Good delivery performance ({profile['delivery_pct']:.1f}%)")
        elif profile['delivery_pct'] < 80:
            insights.append("⚠️ Delivery performance needs improvement")
        
        if profile['pending_dn'] > 0:
            insights.append(f"⏳ {profile['pending_dn']} pending delivery notes")
        
        if profile['warehouse_count'] > 3:
            insights.append(f"🏭 Strong warehouse network: {profile['warehouse_count']} warehouses")
        
        if profile['product_count'] > 10:
            insights.append(f"📦 Wide product portfolio: {profile['product_count']} products")
        
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
        if profile.get('code') and profile['code'] != "N/A":
            lines.append(f"Code: {profile['code']}")
        if profile.get('customer_code') and profile['customer_code'] != "N/A":
            lines.append(f"Customer Code: {profile['customer_code']}")
        if profile.get('office') and profile['office'] != "N/A":
            lines.append(f"Office: {profile['office']}")
        if profile.get('manager') and profile['manager'] != "N/A":
            lines.append(f"Manager: {profile['manager']}")
        if profile.get('division') and profile['division'] != "N/A":
            lines.append(f"Division: {profile['division']}")
        lines.append("")
        
        # Location
        lines.append("📍 LOCATION")
        if profile.get('warehouse') and profile['warehouse'] != "N/A":
            lines.append(f"Warehouse: {profile['warehouse']}")
        if profile.get('warehouse_code') and profile['warehouse_code'] != "N/A":
            lines.append(f"Warehouse Code: {profile['warehouse_code']}")
        if profile.get('city') and profile['city'] != "N/A":
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
        if profile.get('top_product') and profile['top_product'] != "N/A":
            lines.append(f"Top Product: {profile['top_product']}")
        lines.append("")
        
        # Warehouses
        lines.append("🏭 WAREHOUSES")
        lines.append(f"Warehouses: {format_number(profile['warehouse_count'])}")
        if profile.get('warehouses_used'):
            display = [w for w in profile['warehouses_used'] if w != "N/A"][:3]
            if display:
                lines.append(f"Used: {', '.join(display)}")
                if len(profile['warehouses_used']) > 3:
                    lines.append(f"... and {len(profile['warehouses_used']) - 3} more")
        lines.append("")
        
        # Cities
        lines.append("🏙️ CITIES")
        lines.append(f"Cities Served: {format_number(profile['city_count'])}")
        if profile.get('cities_served'):
            display = [c for c in profile['cities_served'] if c != "N/A"][:3]
            if display:
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
        if profile.get('first_order') and profile['first_order'] != "N/A":
            lines.append(f"First Order: {profile['first_order']}")
        if profile.get('last_order') and profile['last_order'] != "N/A":
            lines.append(f"Last Order: {profile['last_order']}")
        if profile.get('latest_pod') and profile['latest_pod'] != "N/A":
            lines.append(f"Latest POD: {profile['latest_pod']}")
        if profile.get('latest_activity') and profile['latest_activity'] != "N/A":
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
            "  • Arshad Electronics-Khi",
            "  • Zoom Appliances",
            "  • RUBA Digital",
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
    print("DEALER ANALYTICS SERVICE - TEST MODE".center(60))
    print("=" * 60)
    print()
    
    service = get_dealer_service()
    
    # Show health
    health = service.health_check()
    print(f"📊 Health: {health}")
    print()
    
    # Test queries
    test_queries = ["arshad", "super trading", "k3 electronics", "zoom"]
    
    for query in test_queries:
        print(f"\n🔍 Testing: '{query}'")
        print("-" * 40)
        result = service.process_whatsapp_query(query, "test_user")
        if result == EXIT_SIGNAL:
            print("EXIT_SIGNAL received")
        elif len(result) > 500:
            print(result[:500] + "...")
        else:
            print(result)
        print()
    
    print("=" * 60)
    print("✅ Test Complete")
