# ==========================================================
# FILE: app/services/dealer_analytics_service.py (v5.0 - FULLY INTEGRATED)
# PURPOSE: Dealer 360° Analytics & Dashboard Engine
# VERSION: 5.0 - PRODUCTION READY WITH FULL INTEGRATION
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
import math
import re
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, case, desc, asc, distinct, extract, text

# ==========================================================
# BLOCK 1: IMPORTS - FULL INTEGRATION
# ==========================================================

from app.models import DeliveryReport

# ==========================================================
# ✅ FIXED: Distance Service Integration
# ==========================================================
try:
    from app.services.distance_service import get_distance_service, DistanceService
    DISTANCE_AVAILABLE = True
    logger.info("✅ Distance service available for dealer analytics")
except ImportError as e:
    logger.warning(f"⚠️ Distance service not available: {e}")
    DISTANCE_AVAILABLE = False
    get_distance_service = None
    DistanceService = None

# ==========================================================
# ✅ FIXED: Analytics Service Integration
# ==========================================================
try:
    from app.services.analytics_service import KPIEngine, EntityResolver, SearchEngine, AnalyticsResponse
    ANALYTICS_AVAILABLE = True
    logger.info("✅ Analytics service available for dealer analytics")
except ImportError as e:
    logger.warning(f"⚠️ Analytics service not available: {e}")
    ANALYTICS_AVAILABLE = False
    KPIEngine = None
    EntityResolver = None
    SearchEngine = None
    AnalyticsResponse = None

# ==========================================================
# BLOCK 2: CONSTANTS
# ==========================================================

DISTANCE_CATEGORIES = {
    "Local": (0, 50),
    "Regional": (50, 200),
    "Long Haul": (200, 500),
    "Remote": (500, float('inf'))
}

DISTANCE_RISK_SCORES = {
    "Local": 100,
    "Regional": 80,
    "Long Haul": 60,
    "Remote": 40
}

RISK_LEVELS = {
    "Critical": (0, 25),
    "High": (25, 50),
    "Medium": (50, 75),
    "Low": (75, 101)
}

PERFORMANCE_GRADES = {
    "A+": (95, 101),
    "A": (85, 95),
    "B+": (75, 85),
    "B": (65, 75),
    "C": (50, 65),
    "D": (0, 50)
}


# ==========================================================
# BLOCK 3: DEALER 360° DASHBOARD CLASS (FULLY INTEGRATED)
# ==========================================================

class Dealer360Dashboard:
    """
    Complete Dealer 360° Dashboard with full service integration.
    
    Integrated Services:
    1. AnalyticsService → KPI calculations
    2. DistanceService → Distance calculations
    3. EntityResolver → Dealer resolution
    4. SearchEngine → Search functionality
    
    Architecture Flow:
    ┌─────────────────────────────────────────────────────────────┐
    │              Dealer360Dashboard                            │
    │  - Dealer Profile                                         │
    │  - Business Volume                                        │
    │  - Delivery Status                                        │
    │  - Performance KPIs                                       │
    │  - Distance Analytics                                     │
    │  - Product Analytics                                      │
    │  - City Analytics                                         │
    │  - Aging Analytics                                        │
    │  - Control Tower Alerts                                   │
    │  - Executive Summary                                      │
    │  - Management Insights                                    │
    └─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
    AnalyticsService    DistanceService    PostgreSQL
    (KPI Engine)        (GeoPy/OSRM)      delivery_reports
    """
    
    def __init__(self, db: Session, resolver: Optional['EntityResolver'] = None, search: Optional['SearchEngine'] = None):
        self.db = db
        self.resolver = resolver
        self.search = search
        
        # ==========================================================
        # ✅ FIXED: Initialize Distance Service
        # ==========================================================
        self.distance_service = None
        if DISTANCE_AVAILABLE and get_distance_service:
            try:
                self.distance_service = get_distance_service()
                logger.info("✅ Distance service initialized in Dealer360Dashboard")
            except Exception as e:
                logger.error(f"❌ Distance service init failed: {e}")
                self.distance_service = None
        
        # ==========================================================
        # ✅ FIXED: Initialize KPI Engine from AnalyticsService
        # ==========================================================
        self.kpi = KPIEngine() if KPIEngine else None
        if self.kpi:
            logger.info("✅ KPI Engine initialized in Dealer360Dashboard")
        else:
            logger.warning("⚠️ KPI Engine not available")
        
        # ==========================================================
        # ✅ FIXED: Import AnalyticsService methods
        # ==========================================================
        self._init_analytics_methods()
    
    def _init_analytics_methods(self):
        """Import analytics methods from AnalyticsService if available."""
        self._analytics_methods = {}
        
        if ANALYTICS_AVAILABLE:
            try:
                from app.services.analytics_service import get_analytics_service
                analytics = get_analytics_service()
                
                # Map methods
                self._analytics_methods = {
                    'calculate_delivery_rate': getattr(analytics, 'calculate_delivery_rate', None),
                    'calculate_pgi_rate': getattr(analytics, 'calculate_pgi_rate', None),
                    'calculate_pod_rate': getattr(analytics, 'calculate_pod_rate', None),
                    'calculate_health_score': getattr(analytics, 'calculate_health_score', None),
                    'calculate_risk_level': getattr(analytics, 'calculate_risk_level', None)
                }
                logger.info("✅ Analytics methods loaded")
            except Exception as e:
                logger.warning(f"⚠️ Could not load analytics methods: {e}")
        
        # Fallback methods if KPIEngine available
        if self.kpi:
            self._analytics_methods.update({
                'calculate_delivery_rate': self.kpi.calculate_delivery_rate,
                'calculate_pgi_rate': self.kpi.calculate_pgi_rate,
                'calculate_pod_rate': self.kpi.calculate_pod_rate,
                'calculate_health_score': self.kpi.calculate_health_score,
                'calculate_risk_level': self.kpi.calculate_risk_level
            })
            logger.info("✅ KPI Engine methods loaded as fallback")


# ==========================================================
# BLOCK 4: GET DASHBOARD - MAIN ENTRY POINT (FIXED)
# ==========================================================

    def get_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get complete 360° dealer dashboard with full service integration.
        
        Returns:
            Complete dashboard with all sections
        """
        import time
        start_time = time.time()
        
        try:
            logger.info(f"🔍 Building 360° dashboard for: '{dealer_name}'")
            
            # ==========================================================
            # STEP 1: Resolve dealer
            # ==========================================================
            resolved, match_type, score = self._resolve_dealer_enhanced(dealer_name)
            
            if not resolved:
                return self._handle_not_found(dealer_name)
            
            logger.info(f"✅ Dealer resolved: '{resolved}' (Match: {match_type}, Score: {score})")
            
            # ==========================================================
            # STEP 2: Get Dealer Profile
            # ==========================================================
            dealer_profile = self._get_dealer_profile(resolved)
            
            # ==========================================================
            # STEP 3: Query all delivery data
            # ==========================================================
            query_start = time.time()
            data = self._query_all_dealer_data(resolved)
            query_time = time.time() - query_start
            logger.info(f"⏱️ Query time: {query_time:.3f}s")
            
            # ==========================================================
            # STEP 4: Merge profile with data
            # ==========================================================
            data.update(dealer_profile)
            data['_match_type'] = match_type
            data['_match_score'] = score
            
            # ==========================================================
            # STEP 5: Build ALL sections
            # ==========================================================
            dashboard = {}
            
            # Section 1: Dealer Profile
            dashboard['profile'] = self._build_profile(resolved, data)
            
            # Section 2: Business Volume
            dashboard['business_volume'] = self._build_business_volume(data)
            
            # Section 3: Delivery Status
            dashboard['delivery_status'] = self._build_delivery_status(data)
            
            # Section 4: POD Status
            dashboard['pod_status'] = self._build_pod_status(data)
            
            # Section 5: PGI Status
            dashboard['pgi_status'] = self._build_pgi_status(data)
            
            # Section 6: Performance KPIs (ENHANCED with KPI Engine)
            dashboard['performance'] = self._build_performance_enhanced(data)
            
            # Section 7: Distance Analytics (FULLY INTEGRATED)
            dashboard['distance'] = self._build_distance_enhanced(data)
            
            # Section 8: Product Analytics
            dashboard['products'] = self._build_product_analytics_enhanced(resolved)
            
            # Section 9: City Analytics
            dashboard['cities'] = self._build_city_analytics_enhanced(resolved)
            
            # Section 10: Aging Analytics
            dashboard['aging'] = self._build_aging_analytics_enhanced(resolved)
            
            # Section 11: Control Tower Alerts
            dashboard['alerts'] = self._build_alerts(dashboard)
            
            # Section 12: Executive Summary
            dashboard['executive_summary'] = self._build_executive_summary(dashboard)
            
            # Section 13: Management Insights
            dashboard['insights'] = self._build_insights(dashboard)
            
            # Section 14: Integration Status
            dashboard['integration_status'] = self._build_integration_status()
            
            # Add warning if no data
            if data.get('total_dns', 0) == 0:
                dashboard['_warning'] = f"No delivery data found for '{resolved}'"
                dashboard['_suggestion'] = "Add Delivery Notes to see analytics"
            
            # Add timestamp
            dashboard['generated_at'] = datetime.now().isoformat()
            dashboard['_dashboard_type'] = '360'
            
            total_time = time.time() - start_time
            logger.info(f"✅ 360° dashboard built in {total_time:.3f}s")
            
            return dashboard
            
        except Exception as e:
            logger.error(f"❌ Dashboard error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"error": f"Failed to load dealer data: {str(e)[:100]}"}


# ==========================================================
# BLOCK 5: ENHANCED DEALER RESOLVER (FIXED)
# ==========================================================

    def _resolve_dealer_enhanced(self, dealer_name: str) -> Tuple[Optional[str], str, float]:
        """
        Enhanced dealer resolution with multiple strategies.
        Uses EntityResolver if available, otherwise fallback to direct queries.
        
        Returns:
            (matched_dealer_name, match_type, confidence_score)
        """
        if not dealer_name or not dealer_name.strip():
            return None, "none", 0.0
        
        search_term = dealer_name.strip()
        logger.info(f"🔍 Enhanced dealer search for: '{search_term}'")
        
        try:
            # ==========================================================
            # Strategy 1: Use EntityResolver if available
            # ==========================================================
            if self.resolver and hasattr(self.resolver, 'resolve_dealer'):
                try:
                    resolved = self.resolver.resolve_dealer(search_term)
                    if resolved:
                        logger.info(f"✅ EntityResolver found: '{resolved}' (100%)")
                        return resolved, "entity_resolver", 100.0
                except Exception as e:
                    logger.warning(f"⚠️ EntityResolver failed: {e}")
            
            # ==========================================================
            # Strategy 2: Exact Match
            # ==========================================================
            result = self.db.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name == search_term
            ).first()
            if result and result[0]:
                logger.info(f"✅ Exact match: '{result[0]}' (100%)")
                return result[0], "exact", 100.0
            
            # ==========================================================
            # Strategy 3: Case Insensitive Match
            # ==========================================================
            result = self.db.query(DeliveryReport.customer_name).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(search_term)
            ).first()
            if result and result[0]:
                logger.info(f"✅ Case insensitive match: '{result[0]}' (95%)")
                return result[0], "case_insensitive", 95.0
            
            # ==========================================================
            # Strategy 4: ILIKE Match
            # ==========================================================
            result = self.db.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name.ilike(f"%{search_term}%")
            ).first()
            if result and result[0]:
                logger.info(f"✅ ILIKE match: '{result[0]}' (85%)")
                return result[0], "ilike", 85.0
            
            # ==========================================================
            # Strategy 5: Multi-Token Search
            # ==========================================================
            tokens = re.sub(r'[^a-zA-Z0-9\s]', '', search_term).split()
            if len(tokens) > 1:
                conditions = []
                for token in tokens:
                    if len(token) > 2:
                        conditions.append(
                            DeliveryReport.customer_name.ilike(f"%{token}%")
                        )
                if conditions:
                    result = self.db.query(DeliveryReport.customer_name).filter(
                        or_(*conditions)
                    ).first()
                    if result and result[0]:
                        logger.info(f"✅ Multi-token match: '{result[0]}' (75%)")
                        return result[0], "multi_token", 75.0
            
            # ==========================================================
            # Strategy 6: Token Search
            # ==========================================================
            for token in tokens:
                if len(token) > 2:
                    result = self.db.query(DeliveryReport.customer_name).filter(
                        DeliveryReport.customer_name.ilike(f"%{token}%")
                    ).first()
                    if result and result[0]:
                        logger.info(f"✅ Token match '{token}': '{result[0]}' (70%)")
                        return result[0], "token", 70.0
            
            logger.warning(f"❌ No match found for: '{search_term}'")
            return None, "none", 0.0
            
        except Exception as e:
            logger.error(f"❌ Enhanced dealer resolution error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None, "error", 0.0


# ==========================================================
# BLOCK 6: GET DEALER PROFILE (FIXED)
# ==========================================================

    def _get_dealer_profile(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get dealer profile from DeliveryReport table.
        Uses DISTINCT ON to get the latest non-null values.
        """
        profile = {
            "dealer_code": "Not Set",
            "customer_code": "Not Set",
            "division": "Not Set",
            "sales_office": "Not Set",
            "sales_manager": "Not Set",
            "warehouse": "Not Set",
            "city": "Not Set",
            "region": "Not Set"
        }
        
        try:
            # Get the latest record for this dealer
            result = self.db.query(
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.division,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.warehouse,
                DeliveryReport.ship_to_city,
                DeliveryReport.dn_create_date
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name)
            ).order_by(
                DeliveryReport.dn_create_date.desc()
            ).first()
            
            if result:
                profile = {
                    "dealer_code": result[0] if result[0] else "Not Set",
                    "customer_code": result[1] if result[1] else "Not Set",
                    "division": result[2] if result[2] else "Not Set",
                    "sales_office": result[3] if result[3] else "Not Set",
                    "sales_manager": result[4] if result[4] else "Not Set",
                    "warehouse": result[5] if result[5] else "Not Set",
                    "city": result[6] if result[6] else "Not Set",
                    "region": "Not Set"
                }
                logger.info(f"✅ Profile found: {profile['dealer_code']}")
            else:
                logger.warning(f"⚠️ No profile found for dealer: {dealer_name}")
                    
        except Exception as e:
            logger.error(f"Profile query error: {e}")
        
        return profile


# ==========================================================
# BLOCK 7: BUILD PROFILE SECTION
# ==========================================================

    def _build_profile(self, dealer_name: str, data: Dict) -> Dict[str, Any]:
        """Build dealer profile section."""
        first_dn = data.get('first_dn_date')
        latest_dn = data.get('latest_dn_date')
        
        return {
            "dealer_name": dealer_name,
            "dealer_code": data.get('dealer_code', 'Not Set'),
            "customer_code": data.get('customer_code', 'Not Set'),
            "division": data.get('division', 'Not Set'),
            "sales_office": data.get('sales_office', 'Not Set'),
            "sales_manager": data.get('sales_manager', 'Not Set'),
            "warehouse": data.get('warehouse', 'Not Set'),
            "city": data.get('city', 'Not Set'),
            "region": data.get('region', 'Not Set'),
            "first_dn_date": first_dn.isoformat() if first_dn else 'Not Set',
            "latest_dn_date": latest_dn.isoformat() if latest_dn else 'Not Set',
            "total_active_days": self._calculate_active_days(first_dn, latest_dn),
            "match_type": data.get('_match_type', 'unknown'),
            "match_score": data.get('_match_score', 0)
        }
    
    def _calculate_active_days(self, first_date: Optional[datetime], latest_date: Optional[datetime]) -> int:
        """Calculate total active days."""
        if not first_date or not latest_date:
            return 0
        return max(0, (latest_date - first_date).days + 1)


# ==========================================================
# BLOCK 8: BUILD BUSINESS VOLUME
# ==========================================================

    def _build_business_volume(self, data: Dict) -> Dict[str, Any]:
        """Build business volume section."""
        total_dns = data.get('total_dns', 0)
        total_units = data.get('total_units', 0)
        total_revenue = data.get('total_revenue', 0)
        
        return {
            "total_dns": total_dns,
            "total_units": total_units,
            "total_revenue": round(total_revenue, 0),
            "avg_revenue_per_dn": round(total_revenue / total_dns, 0) if total_dns > 0 else 0,
            "avg_revenue_per_unit": round(total_revenue / total_units, 0) if total_units > 0 else 0,
            "highest_value_dn": data.get('highest_dn', {}),
            "lowest_value_dn": data.get('lowest_dn', {})
        }


# ==========================================================
# BLOCK 9: BUILD DELIVERY STATUS
# ==========================================================

    def _build_delivery_status(self, data: Dict) -> Dict[str, Any]:
        """Build delivery status section."""
        pending_pgi = data.get('pending_pgi_dns', 0)
        in_transit = data.get('transit_dns', 0)
        delivered = data.get('delivered_dns', 0)
        total_dns = data.get('total_dns', 0)
        
        # Calculate percentages
        pending_pgi_pct = round(pending_pgi / total_dns * 100, 1) if total_dns > 0 else 0
        in_transit_pct = round(in_transit / total_dns * 100, 1) if total_dns > 0 else 0
        delivered_pct = round(delivered / total_dns * 100, 1) if total_dns > 0 else 0
        
        return {
            "pending_pgi": pending_pgi,
            "in_transit": in_transit,
            "delivered": delivered,
            "total": total_dns,
            "pending_pgi_percent": pending_pgi_pct,
            "in_transit_percent": in_transit_pct,
            "delivered_percent": delivered_pct,
            "status_buckets_summary": f"{pending_pgi} + {in_transit} + {delivered} = {pending_pgi + in_transit + delivered}"
        }


# ==========================================================
# BLOCK 10: BUILD POD STATUS
# ==========================================================

    def _build_pod_status(self, data: Dict) -> Dict[str, Any]:
        """Build POD status section."""
        pod_completed = data.get('pod_completed_dns', 0)
        pending_pod = data.get('pending_pod_dns', 0)
        delivered = data.get('delivered_dns', 0)
        
        pod_compliance = min(round(pod_completed / delivered * 100, 1) if delivered > 0 else 0, 100.0)
        
        return {
            "pod_completed": pod_completed,
            "pending_pod": pending_pod,
            "pod_compliance": pod_compliance,
            "avg_pod_days": data.get('avg_pod_days', 0),
            "oldest_pending_pod": data.get('oldest_pending_pod', 'N/A')
        }


# ==========================================================
# BLOCK 11: BUILD PGI STATUS
# ==========================================================

    def _build_pgi_status(self, data: Dict) -> Dict[str, Any]:
        """Build PGI status section."""
        pgi_completed = data.get('pgi_completed_dns', 0)
        pending_pgi = data.get('pending_pgi_dns', 0)
        total_dns = data.get('total_dns', 0)
        
        pgi_compliance = min(round(pgi_completed / total_dns * 100, 1) if total_dns > 0 else 0, 100.0)
        
        return {
            "pgi_completed": pgi_completed,
            "pending_pgi": pending_pgi,
            "pgi_compliance": pgi_compliance,
            "avg_pgi_days": data.get('avg_pgi_days', 0),
            "oldest_pending_pgi": data.get('oldest_pending_pgi', 'N/A')
        }


# ==========================================================
# BLOCK 12: BUILD PERFORMANCE KPIs (ENHANCED WITH KPI ENGINE)
# ==========================================================

    def _build_performance_enhanced(self, data: Dict) -> Dict[str, Any]:
        """
        Build enhanced performance KPIs using KPI Engine.
        Integrated with AnalyticsService KPIEngine.
        """
        total_dns = data.get('total_dns', 0)
        delivered = data.get('delivered_dns', 0)
        transit = data.get('transit_dns', 0)
        pod_completed = data.get('pod_completed_dns', 0)
        total_revenue = data.get('total_revenue', 0)
        total_units = data.get('total_units', 0)
        
        # ==========================================================
        # Use KPI Engine for calculations
        # ==========================================================
        if self.kpi:
            delivery_rate = min(self.kpi.calculate_delivery_rate(delivered, total_dns), 100.0)
            pgi_rate = min(self.kpi.calculate_pgi_rate(delivered, transit, total_dns), 100.0)
            pod_rate = min(self.kpi.calculate_pod_rate(pod_completed, delivered), 100.0) if delivered > 0 else 0
            
            health_score = self.kpi.calculate_health_score({
                "delivery_rate": delivery_rate,
                "pod_rate": pod_rate,
                "avg_aging": data.get('avg_delivery_days', 0),
                "revenue": total_revenue
            })
            
            risk_level, risk_score = self.kpi.calculate_risk_level(delivery_rate, pod_rate, 0)
        else:
            # Fallback calculations
            delivery_rate = min(round(delivered / total_dns * 100, 1) if total_dns > 0 else 0, 100.0)
            pgi_rate = min(round((delivered + transit) / total_dns * 100, 1) if total_dns > 0 else 0, 100.0)
            pod_rate = min(round(pod_completed / delivered * 100, 1) if delivered > 0 else 0, 100.0)
            health_score = min(round((delivery_rate * 0.4 + pod_rate * 0.3 + 30), 0), 100)
            risk_score = 100 - health_score
            risk_level = self._get_risk_level(risk_score)
        
        # Distance risk
        distance_data = data.get('distance_data', {})
        distance_risk_score = distance_data.get('risk_score', 50)
        
        # Combined risk
        combined_risk_score = int((risk_score + (100 - distance_risk_score)) / 2)
        
        return {
            "delivery_rate": delivery_rate,
            "pgi_rate": pgi_rate,
            "pod_rate": pod_rate,
            "health_score": health_score,
            "risk_score": combined_risk_score,
            "risk_level": self._get_risk_level(combined_risk_score),
            "performance_grade": self._get_performance_grade(health_score),
            "distance_risk_score": distance_risk_score,
            "revenue_efficiency": {
                "avg_revenue_per_dn": round(total_revenue / total_dns, 0) if total_dns > 0 else 0,
                "avg_revenue_per_unit": round(total_revenue / total_units, 0) if total_units > 0 else 0
            }
        }
    
    def _get_performance_grade(self, health_score: int) -> str:
        """Get performance grade based on health score."""
        for grade, (min_score, max_score) in PERFORMANCE_GRADES.items():
            if min_score <= health_score < max_score:
                return grade
        return "D"
    
    def _get_risk_level(self, risk_score: int) -> str:
        """Get risk level based on risk score."""
        for level, (min_score, max_score) in RISK_LEVELS.items():
            if min_score <= risk_score < max_score:
                return level
        return "Unknown"


# ==========================================================
# BLOCK 13: BUILD DISTANCE ANALYTICS (FULLY INTEGRATED)
# ==========================================================

    def _build_distance_enhanced(self, data: Dict) -> Dict[str, Any]:
        """
        Build enhanced distance analytics with full DistanceService integration.
        
        Integrated with:
        - distance_service.py → calculate_warehouse_distance()
        - distance_service.py → get_warehouse_coverage()
        """
        warehouse = data.get('warehouse')
        city = data.get('city')
        
        result = {
            "warehouse": warehouse or 'N/A',
            "dealer_city": city or 'N/A',
            "road_distance_km": None,
            "air_distance_km": None,
            "estimated_hours": None,
            "distance_category": 'N/A',
            "risk_score": 50,
            "distance_available": False,
            "message": "Distance data not available",
            "integration_status": "DistanceService not available"
        }
        
        if warehouse and city and warehouse != 'Not Set' and city != 'Not Set':
            # ==========================================================
            # Try using DistanceService
            # ==========================================================
            if self.distance_service and hasattr(self.distance_service, 'calculate_warehouse_distance'):
                try:
                    logger.info(f"📍 Calculating distance: {warehouse} → {city}")
                    distance_info = self.distance_service.calculate_warehouse_distance(warehouse, city)
                    
                    if distance_info and distance_info.get('success'):
                        distance_km = distance_info.get('distance_km', 0)
                        driving_hours = distance_info.get('approx_driving_hours', 0)
                        driving_minutes = distance_info.get('approx_driving_minutes', 0)
                        
                        result['road_distance_km'] = round(distance_km, 1)
                        result['air_distance_km'] = round(distance_km * 0.85, 1)  # Estimate
                        
                        if driving_hours and driving_hours > 0:
                            result['estimated_hours'] = round(driving_hours, 1)
                        elif driving_minutes and driving_minutes > 0:
                            result['estimated_minutes'] = driving_minutes
                        
                        result['distance_category'] = self._get_distance_category(distance_km)
                        result['risk_score'] = self._get_distance_risk_score(result['distance_category'])
                        result['distance_available'] = True
                        result['message'] = f"Distance calculated: {distance_km} km"
                        result['integration_status'] = "DistanceService ✅"
                        
                        logger.info(f"✅ Distance: {distance_km} km ({result['distance_category']})")
                        
                        # Try to get coverage info
                        if hasattr(self.distance_service, 'get_warehouse_coverage'):
                            try:
                                coverage = self.distance_service.get_warehouse_coverage(warehouse)
                                if coverage and coverage.get('success'):
                                    result['coverage'] = {
                                        'avg_distance': coverage.get('avg_distance_km'),
                                        'max_distance': coverage.get('max_distance_km'),
                                        'cities': coverage.get('cities', [])
                                    }
                                    logger.info(f"✅ Coverage info added")
                            except Exception as e:
                                logger.warning(f"Coverage info failed: {e}")
                    else:
                        result['message'] = distance_info.get('error', 'Distance calculation failed')
                        result['integration_status'] = f"DistanceService error: {result['message']}"
                        logger.warning(f"⚠️ Distance error: {result['message']}")
                        
                except Exception as e:
                    logger.error(f"❌ Distance calculation error: {e}")
                    result['message'] = str(e)
                    result['integration_status'] = f"DistanceService exception: {str(e)[:50]}"
            else:
                result['integration_status'] = "DistanceService not initialized"
                logger.warning("⚠️ DistanceService not available")
        else:
            result['message'] = "Warehouse or city missing from dealer profile"
            result['integration_status'] = "Missing data (warehouse/city)"
        
        return result
    
    def _get_distance_category(self, distance_km: float) -> str:
        """Get distance category based on distance."""
        if distance_km is None or distance_km <= 0:
            return "Unknown"
        
        for category, (min_dist, max_dist) in DISTANCE_CATEGORIES.items():
            if min_dist <= distance_km < max_dist:
                return category
        return "Remote"
    
    def _get_distance_risk_score(self, category: str) -> int:
        """Get risk score based on distance category."""
        return DISTANCE_RISK_SCORES.get(category, 50)


# ==========================================================
# BLOCK 14: BUILD PRODUCT ANALYTICS
# ==========================================================

    def _build_product_analytics_enhanced(self, dealer_name: str) -> Dict[str, Any]:
        """Build enhanced product analytics section."""
        try:
            products = self.db.query(
                DeliveryReport.customer_model,
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count')
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name),
                DeliveryReport.customer_model.isnot(None),
                DeliveryReport.customer_model != ''
            ).group_by(
                DeliveryReport.customer_model
            ).order_by(
                desc('total_revenue')
            ).limit(10).all()
            
            if not products:
                return {
                    "total_products": 0,
                    "top_product": "N/A",
                    "top_10_products": [],
                    "highest_revenue_product": "N/A",
                    "highest_volume_product": "N/A",
                    "product_mix": {}
                }
            
            total_revenue = sum(p.total_revenue or 0 for p in products)
            total_units = sum(p.total_units or 0 for p in products)
            
            top_products = []
            for p in products:
                revenue = p.total_revenue or 0
                units = p.total_units or 0
                dns = p.dn_count or 0
                revenue_share = round(revenue / total_revenue * 100, 1) if total_revenue > 0 else 0
                
                top_products.append({
                    "product": p.customer_model,
                    "revenue": round(revenue, 0),
                    "units": int(units),
                    "dns": int(dns),
                    "revenue_share": revenue_share
                })
            
            highest_revenue = max(products, key=lambda x: x.total_revenue or 0) if products else None
            highest_volume = max(products, key=lambda x: x.total_units or 0) if products else None
            
            return {
                "total_products": len(products),
                "top_product": products[0].customer_model if products else "N/A",
                "top_10_products": top_products,
                "highest_revenue_product": highest_revenue.customer_model if highest_revenue else "N/A",
                "highest_volume_product": highest_volume.customer_model if highest_volume else "N/A",
                "product_mix": {
                    "total_revenue": round(total_revenue, 0),
                    "total_units": int(total_units)
                }
            }
            
        except Exception as e:
            logger.error(f"Product analytics error: {e}")
            return {
                "total_products": 0,
                "top_product": "N/A",
                "top_10_products": [],
                "highest_revenue_product": "N/A",
                "highest_volume_product": "N/A",
                "product_mix": {}
            }


# ==========================================================
# BLOCK 15: BUILD CITY ANALYTICS
# ==========================================================

    def _build_city_analytics_enhanced(self, dealer_name: str) -> Dict[str, Any]:
        """Build enhanced city analytics section."""
        try:
            cities = self.db.query(
                DeliveryReport.ship_to_city,
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count')
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name),
                DeliveryReport.ship_to_city.isnot(None),
                DeliveryReport.ship_to_city != ''
            ).group_by(
                DeliveryReport.ship_to_city
            ).order_by(
                desc('total_revenue')
            ).all()
            
            if not cities:
                return {
                    "cities_served": 0,
                    "top_city": "N/A",
                    "revenue_by_city": {},
                    "units_by_city": {},
                    "city_distances": []
                }
            
            revenue_by_city = {}
            units_by_city = {}
            
            for city in cities:
                revenue_by_city[city.ship_to_city] = round(city.total_revenue or 0, 0)
                units_by_city[city.ship_to_city] = int(city.total_units or 0)
            
            return {
                "cities_served": len(cities),
                "top_city": cities[0].ship_to_city if cities else "N/A",
                "revenue_by_city": revenue_by_city,
                "units_by_city": units_by_city,
                "city_distances": []
            }
            
        except Exception as e:
            logger.error(f"City analytics error: {e}")
            return {
                "cities_served": 0,
                "top_city": "N/A",
                "revenue_by_city": {},
                "units_by_city": {},
                "city_distances": []
            }


# ==========================================================
# BLOCK 16: BUILD AGING ANALYTICS
# ==========================================================

    def _build_aging_analytics_enhanced(self, dealer_name: str) -> Dict[str, Any]:
        """Build enhanced aging analytics section."""
        try:
            result = self.db.query(
                func.avg(
                    func.extract('day', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                ).label('avg_delivery_days'),
                func.min(
                    func.extract('day', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                ).label('min_delivery_days'),
                func.max(
                    func.extract('day', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                ).label('max_delivery_days'),
                func.avg(
                    func.extract('day', DeliveryReport.pod_date - DeliveryReport.good_issue_date)
                ).label('avg_pod_days'),
                func.avg(
                    func.extract('day', DeliveryReport.pod_date - DeliveryReport.dn_create_date)
                ).label('avg_cycle_days'),
                func.max(
                    func.extract('day', func.now() - DeliveryReport.good_issue_date)
                ).label('oldest_pending_pod')
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name),
                DeliveryReport.dn_create_date.isnot(None)
            ).first()
            
            if not result:
                return {
                    "avg_delivery_days": 0,
                    "min_delivery_days": 0,
                    "max_delivery_days": 0,
                    "avg_pod_days": 0,
                    "avg_cycle_days": 0,
                    "oldest_pending_pod": 'N/A'
                }
            
            oldest_pending = 'N/A'
            if result.oldest_pending_pod:
                days = int(result.oldest_pending_pod or 0)
                oldest_pending = f"{days} days"
            
            return {
                "avg_delivery_days": round(result.avg_delivery_days or 0, 1),
                "min_delivery_days": int(result.min_delivery_days or 0),
                "max_delivery_days": int(result.max_delivery_days or 0),
                "avg_pod_days": round(result.avg_pod_days or 0, 1),
                "avg_cycle_days": round(result.avg_cycle_days or 0, 1),
                "oldest_pending_pod": oldest_pending
            }
            
        except Exception as e:
            logger.error(f"Aging analytics error: {e}")
            return {
                "avg_delivery_days": 0,
                "min_delivery_days": 0,
                "max_delivery_days": 0,
                "avg_pod_days": 0,
                "avg_cycle_days": 0,
                "oldest_pending_pod": 'N/A'
            }


# ==========================================================
# BLOCK 17: BUILD ALERTS
# ==========================================================

    def _build_alerts(self, dashboard: Dict) -> List[Dict[str, Any]]:
        """Build control tower alerts."""
        alerts = []
        performance = dashboard.get('performance', {})
        delivery_status = dashboard.get('delivery_status', {})
        pod_status = dashboard.get('pod_status', {})
        pgi_status = dashboard.get('pgi_status', {})
        distance = dashboard.get('distance', {})
        
        # Alert 1: High Pending PGI
        pending_pgi = delivery_status.get('pending_pgi', 0)
        if pending_pgi > 5:
            alerts.append({
                "type": "High Pending PGI",
                "severity": "high" if pending_pgi > 20 else "medium",
                "message": f"{pending_pgi} DNs pending PGI",
                "recommendation": "Process PGI for pending deliveries"
            })
        
        # Alert 2: High Pending POD
        pending_pod = pod_status.get('pending_pod', 0)
        if pending_pod > 5:
            alerts.append({
                "type": "High Pending POD",
                "severity": "critical" if pending_pod > 30 else "high",
                "message": f"{pending_pod} DNs pending POD",
                "recommendation": "Collect POD for delivered items"
            })
        
        # Alert 3: Low Delivery Performance
        delivery_rate = performance.get('delivery_rate', 0)
        if delivery_rate < 70:
            alerts.append({
                "type": "Low Delivery Performance",
                "severity": "high" if delivery_rate < 50 else "medium",
                "message": f"Delivery rate is {delivery_rate}%",
                "recommendation": "Investigate delivery delays"
            })
        
        # Alert 4: Low POD Compliance
        pod_compliance = pod_status.get('pod_compliance', 0)
        if pod_compliance < 70:
            alerts.append({
                "type": "Low POD Compliance",
                "severity": "high" if pod_compliance < 50 else "medium",
                "message": f"POD compliance is {pod_compliance}%",
                "recommendation": "Improve POD collection process"
            })
        
        # Alert 5: High Risk Dealer
        risk_level = performance.get('risk_level', 'Low')
        if risk_level in ['High', 'Critical']:
            alerts.append({
                "type": "High Risk Dealer",
                "severity": "critical" if risk_level == 'Critical' else "high",
                "message": f"Dealer has {risk_level} risk profile",
                "recommendation": "Review dealer performance and take action"
            })
        
        # Alert 6: Distance Risk
        distance_category = distance.get('distance_category', 'Local')
        if distance_category in ['Long Haul', 'Remote']:
            alerts.append({
                "type": "Distance Risk",
                "severity": "medium" if distance_category == 'Long Haul' else "high",
                "message": f"Dealer is in {distance_category} location",
                "recommendation": "Consider logistics optimization"
            })
        
        # Sort alerts by severity
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        alerts.sort(key=lambda x: severity_order.get(x.get('severity', 'low'), 4))
        
        return alerts


# ==========================================================
# BLOCK 18: BUILD EXECUTIVE SUMMARY
# ==========================================================

    def _build_executive_summary(self, dashboard: Dict) -> str:
        """Build executive summary."""
        profile = dashboard.get('profile', {})
        business = dashboard.get('business_volume', {})
        performance = dashboard.get('performance', {})
        delivery_status = dashboard.get('delivery_status', {})
        pod_status = dashboard.get('pod_status', {})
        distance = dashboard.get('distance', {})
        alerts = dashboard.get('alerts', [])
        
        dealer_name = profile.get('dealer_name', 'Dealer')
        total_revenue = business.get('total_revenue', 0)
        total_dns = business.get('total_dns', 0)
        delivery_rate = performance.get('delivery_rate', 0)
        pod_compliance = pod_status.get('pod_compliance', 0)
        pending_pgi = delivery_status.get('pending_pgi', 0)
        pending_pod = pod_status.get('pending_pod', 0)
        risk_level = performance.get('risk_level', 'Unknown')
        distance_category = distance.get('distance_category', 'N/A')
        
        summary_lines = [
            f"🏢 {dealer_name} generated PKR {total_revenue:,.0f} revenue.",
            f"📦 Total DNs: {total_dns}",
            f"🚚 Delivery Rate: {delivery_rate}%",
            f"📋 POD Compliance: {pod_compliance}%",
            f"⏳ Pending PGI: {pending_pgi}",
            f"⏳ Pending POD: {pending_pod}",
            f"⚠️ Risk Level: {risk_level}",
            f"📍 Distance: {distance_category}"
        ]
        
        if alerts:
            critical_alerts = [a for a in alerts if a.get('severity') == 'critical']
            if critical_alerts:
                summary_lines.append(f"🚨 CRITICAL: {len(critical_alerts)} Critical Alert(s)")
            elif len([a for a in alerts if a.get('severity') == 'high']) > 0:
                summary_lines.append("⚠️ High Priority Alerts - Review Required")
            else:
                summary_lines.append("✅ No Critical Alerts")
        
        return "\n".join(summary_lines)


# ==========================================================
# BLOCK 19: BUILD MANAGEMENT INSIGHTS
# ==========================================================

    def _build_insights(self, dashboard: Dict) -> Dict[str, str]:
        """Build management insights."""
        performance = dashboard.get('performance', {})
        delivery_status = dashboard.get('delivery_status', {})
        pod_status = dashboard.get('pod_status', {})
        business = dashboard.get('business_volume', {})
        distance = dashboard.get('distance', {})
        alerts = dashboard.get('alerts', [])
        
        delivery_rate = performance.get('delivery_rate', 0)
        pod_compliance = pod_status.get('pod_compliance', 0)
        total_revenue = business.get('total_revenue', 0)
        avg_revenue_per_dn = business.get('avg_revenue_per_dn', 0)
        pending_pod = pod_status.get('pending_pod', 0)
        pending_pgi = delivery_status.get('pending_pgi', 0)
        distance_category = distance.get('distance_category', 'Local')
        
        insights = {}
        
        # TOP STRENGTH
        strengths = []
        if delivery_rate >= 90:
            strengths.append("✅ Excellent Delivery Performance (90%+)")
        if pod_compliance >= 90:
            strengths.append("✅ Excellent POD Compliance (90%+)")
        if avg_revenue_per_dn > 50000:
            strengths.append(f"✅ High Value Orders (PKR {avg_revenue_per_dn:,.0f} per DN)")
        if total_revenue > 1000000:
            strengths.append("✅ Strong Revenue Generation (PKR 1M+)")
        if pending_pgi == 0:
            strengths.append("✅ Zero PGI Pending - Excellent processing")
        if pending_pod <= 5:
            strengths.append("✅ Low POD Pending - Good documentation")
        
        insights['top_strength'] = strengths[0] if strengths else "✅ Consistent Operations"
        
        # BIGGEST RISK
        risks = []
        if delivery_rate < 70:
            risks.append(f"🔴 Low Delivery Rate ({delivery_rate}%)")
        if pod_compliance < 70:
            risks.append(f"🔴 Low POD Compliance ({pod_compliance}%)")
        if pending_pod > 20:
            risks.append(f"🔴 High Pending POD ({pending_pod})")
        if pending_pgi > 10:
            risks.append(f"🔴 High Pending PGI ({pending_pgi})")
        if len(alerts) > 3:
            risks.append(f"🔴 Multiple Alerts ({len(alerts)})")
        if distance_category in ['Long Haul', 'Remote']:
            risks.append(f"🔴 {distance_category} Location - Logistics challenges")
        
        insights['biggest_risk'] = risks[0] if risks else "🟢 No Significant Risks Identified"
        
        # RECOMMENDED ACTION
        if alerts:
            critical_alerts = [a for a in alerts if a.get('severity') == 'critical']
            if critical_alerts:
                insights['recommended_action'] = f"🚨 CRITICAL: {critical_alerts[0].get('recommendation', 'Take immediate action')}"
            else:
                high_alerts = [a for a in alerts if a.get('severity') == 'high']
                if high_alerts:
                    insights['recommended_action'] = high_alerts[0].get('recommendation', 'Review and optimize')
                else:
                    insights['recommended_action'] = alerts[0].get('recommendation', 'Review operations')
        else:
            if delivery_rate < 95:
                insights['recommended_action'] = "Aim for 95%+ delivery rate"
            elif pod_compliance < 95:
                insights['recommended_action'] = "Aim for 95%+ POD compliance"
            else:
                insights['recommended_action'] = "Maintain current performance"
        
        # EXPECTED IMPACT
        health_score = performance.get('health_score', 0)
        
        if health_score >= 75:
            insights['expected_impact'] = "High - Strong growth potential"
        elif health_score >= 50:
            insights['expected_impact'] = "Medium - Optimization opportunities"
        else:
            insights['expected_impact'] = "Low - Focus on basic improvements"
        
        return insights


# ==========================================================
# BLOCK 20: BUILD INTEGRATION STATUS
# ==========================================================

    def _build_integration_status(self) -> Dict[str, Any]:
        """Build integration status section."""
        return {
            "distance_service": {
                "available": self.distance_service is not None,
                "status": "✅ Connected" if self.distance_service else "❌ Not Available"
            },
            "kpi_engine": {
                "available": self.kpi is not None,
                "status": "✅ Connected" if self.kpi else "❌ Not Available"
            },
            "resolver": {
                "available": self.resolver is not None,
                "status": "✅ Connected" if self.resolver else "⚠️ Using Fallback"
            }
        }


# ==========================================================
# BLOCK 21: QUERY DATA (OPTIMIZED)
# ==========================================================

    def _query_all_dealer_data(self, dealer_name: str) -> Dict[str, Any]:
        """Query all dealer data from database."""
        try:
            logger.info(f"📊 Querying delivery data for: '{dealer_name}'")
            
            result = self.db.query(
                func.min(DeliveryReport.dn_create_date).label("first_dn_date"),
                func.max(DeliveryReport.dn_create_date).label("latest_dn_date"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("total_revenue"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("transit_dns"),
                func.count(distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_dns"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pending_pod_dns"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pending_pgi_dns"),
                func.count(distinct(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no), else_=None))).label("pgi_completed_dns")
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name)
            ).first()
            
            if not result or result.total_dns == 0:
                logger.warning(f"❌ No data found for dealer '{dealer_name}'")
                return {}
            
            data = {
                "first_dn_date": result.first_dn_date,
                "latest_dn_date": result.latest_dn_date,
                "total_dns": result.total_dns or 0,
                "total_units": result.total_units or 0,
                "total_revenue": result.total_revenue or 0,
                "delivered_dns": result.delivered_dns or 0,
                "pending_dns": result.pending_dns or 0,
                "transit_dns": result.transit_dns or 0,
                "pod_completed_dns": result.pod_completed_dns or 0,
                "pending_pod_dns": result.pending_pod_dns or 0,
                "pending_pgi_dns": result.pending_pgi_dns or 0,
                "pgi_completed_dns": result.pgi_completed_dns or 0
            }
            
            # Get highest and lowest value DNs
            highest_dn = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_amount
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name),
                DeliveryReport.dn_amount.isnot(None)
            ).order_by(
                desc(DeliveryReport.dn_amount)
            ).first()
            
            if highest_dn:
                data['highest_dn'] = {
                    "dn_no": highest_dn.dn_no,
                    "amount": round(highest_dn.dn_amount or 0, 0)
                }
            else:
                data['highest_dn'] = {"dn_no": "N/A", "amount": 0}
            
            lowest_dn = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_amount
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name),
                DeliveryReport.dn_amount.isnot(None),
                DeliveryReport.dn_amount > 0
            ).order_by(
                DeliveryReport.dn_amount
            ).first()
            
            if lowest_dn:
                data['lowest_dn'] = {
                    "dn_no": lowest_dn.dn_no,
                    "amount": round(lowest_dn.dn_amount or 0, 0)
                }
            else:
                data['lowest_dn'] = {"dn_no": "N/A", "amount": 0}
            
            return data
            
        except Exception as e:
            logger.error(f"Query error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}


# ==========================================================
# BLOCK 22: HANDLE NOT FOUND
# ==========================================================

    def _handle_not_found(self, dealer_name: str) -> Dict[str, Any]:
        """Handle dealer not found with suggestions."""
        try:
            if self.search and hasattr(self.search, 'search_dealer'):
                similar = self.search.search_dealer(dealer_name, exact=False)
                if similar and len(similar) > 0:
                    suggestions = [s['dealer_name'] for s in similar[:5]]
                    return {
                        "error": f"Dealer '{dealer_name}' not found",
                        "suggestions": suggestions,
                        "message": f"Did you mean: {', '.join(suggestions[:3])}?"
                    }
        except Exception as e:
            logger.error(f"Search error: {e}")
        
        return {
            "error": f"Dealer '{dealer_name}' not found",
            "message": "Please check the spelling or try a shorter version"
        }


# ==========================================================
# BLOCK 23: FACTORY FUNCTIONS
# ==========================================================

def get_dealer_360_dashboard(db: Session, resolver: Optional['EntityResolver'] = None, search: Optional['SearchEngine'] = None) -> Dealer360Dashboard:
    """
    Factory function to create Dealer360Dashboard instance.
    Integrated with AnalyticsService and DistanceService.
    """
    return Dealer360Dashboard(db, resolver, search)


# ==========================================================
# BLOCK 24: WHATSAPP FORMATTER (FIXED)
# ==========================================================

def format_dealer_360_dashboard(dashboard: Dict[str, Any]) -> str:
    """
    Format 360° dashboard for WhatsApp display.
    Shows all sections including distance analytics.
    """
    if not dashboard:
        return "❌ No data available"
    
    if "error" in dashboard:
        error = dashboard.get("error", "Unknown error")
        suggestions = dashboard.get("suggestions", [])
        if suggestions:
            return f"❌ {error}\n\n💡 Did you mean:\n" + "\n".join([f"• {s}" for s in suggestions[:3]])
        return f"❌ {error}"
    
    lines = []
    
    # ==========================================================
    # SECTION 1: DEALER PROFILE
    # ==========================================================
    profile = dashboard.get('profile', {})
    lines.append("🏢 *DEALER 360° DASHBOARD*")
    lines.append("")
    lines.append("📋 *PROFILE*")
    lines.append(f"Dealer: {profile.get('dealer_name', 'N/A')}")
    lines.append(f"Dealer Code: {profile.get('dealer_code', 'N/A')}")
    lines.append(f"Customer Code: {profile.get('customer_code', 'N/A')}")
    lines.append(f"Division: {profile.get('division', 'N/A')}")
    lines.append(f"Sales Office: {profile.get('sales_office', 'N/A')}")
    lines.append(f"Sales Manager: {profile.get('sales_manager', 'N/A')}")
    lines.append(f"Warehouse: {profile.get('warehouse', 'N/A')}")
    lines.append(f"City: {profile.get('city', 'N/A')}")
    lines.append(f"Active Days: {profile.get('total_active_days', 0)}")
    
    match_type = profile.get('match_type', '')
    if match_type and match_type != 'unknown':
        lines.append(f"📌 Match: {match_type.replace('_', ' ').title()}")
    
    # ==========================================================
    # SECTION 2: BUSINESS VOLUME
    # ==========================================================
    business = dashboard.get('business_volume', {})
    lines.append("")
    lines.append("💰 *BUSINESS VOLUME*")
    lines.append(f"Total DNs: {business.get('total_dns', 0)}")
    lines.append(f"Total Units: {business.get('total_units', 0)}")
    
    revenue = business.get('total_revenue', 0)
    if revenue:
        lines.append(f"Total Revenue: PKR {revenue:,.0f}")
        lines.append(f"Avg per DN: PKR {business.get('avg_revenue_per_dn', 0):,.0f}")
    else:
        lines.append(f"Total Revenue: PKR 0")
    
    # ==========================================================
    # SECTION 3: DELIVERY STATUS
    # ==========================================================
    delivery = dashboard.get('delivery_status', {})
    lines.append("")
    lines.append("📦 *DELIVERY STATUS*")
    lines.append(f"✅ Delivered: {delivery.get('delivered', 0)} ({delivery.get('delivered_percent', 0)}%)")
    lines.append(f"🚚 In Transit: {delivery.get('in_transit', 0)} ({delivery.get('in_transit_percent', 0)}%)")
    lines.append(f"⏳ Pending PGI: {delivery.get('pending_pgi', 0)} ({delivery.get('pending_pgi_percent', 0)}%)")
    lines.append(f"📊 Total: {delivery.get('total', 0)}")
    
    # ==========================================================
    # SECTION 4: POD STATUS
    # ==========================================================
    pod = dashboard.get('pod_status', {})
    lines.append("")
    lines.append("📋 *POD STATUS*")
    lines.append(f"POD Completed: {pod.get('pod_completed', 0)}")
    lines.append(f"Pending POD: {pod.get('pending_pod', 0)}")
    lines.append(f"POD Compliance: {pod.get('pod_compliance', 0)}%")
    
    # ==========================================================
    # SECTION 5: PERFORMANCE KPIs
    # ==========================================================
    perf = dashboard.get('performance', {})
    lines.append("")
    lines.append("⚡ *PERFORMANCE*")
    lines.append(f"Delivery Rate: {perf.get('delivery_rate', 0)}%")
    lines.append(f"PGI Rate: {perf.get('pgi_rate', 0)}%")
    lines.append(f"POD Rate: {perf.get('pod_rate', 0)}%")
    lines.append(f"Health Score: {perf.get('health_score', 0)}/100")
    lines.append(f"Risk Level: {perf.get('risk_level', 'Unknown')}")
    lines.append(f"Performance Grade: {perf.get('performance_grade', 'N/A')}")
    
    # ==========================================================
    # SECTION 6: DISTANCE ANALYTICS
    # ==========================================================
    distance = dashboard.get('distance', {})
    lines.append("")
    lines.append("📍 *DISTANCE ANALYTICS*")
    lines.append(f"Warehouse: {distance.get('warehouse', 'N/A')}")
    lines.append(f"Dealer City: {distance.get('dealer_city', 'N/A')}")
    
    road_distance = distance.get('road_distance_km')
    if road_distance:
        lines.append("")
        lines.append(f"🚗 Road Distance: {road_distance} KM")
        if distance.get('estimated_hours'):
            hours = distance.get('estimated_hours')
            if hours < 1:
                lines.append(f"⏱️ Transit Time: {int(hours * 60)} minutes")
            else:
                lines.append(f"⏱️ Transit Time: {hours} hours")
        lines.append(f"📌 Category: {distance.get('distance_category', 'N/A')}")
        lines.append(f"🎯 Risk Score: {distance.get('risk_score', 50)}/100")
    else:
        lines.append("")
        lines.append("📌 Distance: Not calculated")
    
    # ==========================================================
    # SECTION 7: PRODUCT ANALYTICS
    # ==========================================================
    products = dashboard.get('products', {})
    lines.append("")
    lines.append("📦 *PRODUCTS*")
    lines.append(f"Total Products: {products.get('total_products', 0)}")
    lines.append(f"Top Product: {products.get('top_product', 'N/A')}")
    
    top_10 = products.get('top_10_products', [])
    if top_10:
        lines.append("")
        lines.append("🏆 *Top Products*")
        for i, p in enumerate(top_10[:5], 1):
            revenue = p.get('revenue', 0)
            share = p.get('revenue_share', 0)
            lines.append(f"{i}. {p.get('product', 'N/A')} (PKR {revenue:,.0f}, {share}%)")
    
    # ==========================================================
    # SECTION 8: AGING ANALYTICS
    # ==========================================================
    aging = dashboard.get('aging', {})
    lines.append("")
    lines.append("⏳ *AGING*")
    lines.append(f"Avg Delivery Days: {aging.get('avg_delivery_days', 0)}")
    lines.append(f"Avg POD Days: {aging.get('avg_pod_days', 0)}")
    lines.append(f"Avg Cycle Days: {aging.get('avg_cycle_days', 0)}")
    lines.append(f"Oldest Pending: {aging.get('oldest_pending_pod', 'N/A')}")
    
    # ==========================================================
    # SECTION 9: ALERTS
    # ==========================================================
    alerts = dashboard.get('alerts', [])
    if alerts:
        lines.append("")
        lines.append("🚨 *ALERTS*")
        for alert in alerts[:5]:
            severity = alert.get('severity', 'low')
            emoji = "🔴" if severity == 'critical' else "🟠" if severity == 'high' else "🟡"
            lines.append(f"{emoji} {alert.get('message', '')}")
    
    # ==========================================================
    # SECTION 10: EXECUTIVE SUMMARY
    # ==========================================================
    summary = dashboard.get('executive_summary', '')
    if summary:
        lines.append("")
        lines.append("📌 *EXECUTIVE SUMMARY*")
        for line in summary.split('\n'):
            lines.append(line)
    
    # ==========================================================
    # SECTION 11: MANAGEMENT INSIGHTS
    # ==========================================================
    insights = dashboard.get('insights', {})
    if insights:
        lines.append("")
        lines.append("💡 *MANAGEMENT INSIGHTS*")
        lines.append(f"✅ Strength: {insights.get('top_strength', 'N/A')}")
        lines.append(f"⚠️ Risk: {insights.get('biggest_risk', 'N/A')}")
        lines.append(f"🎯 Action: {insights.get('recommended_action', 'N/A')}")
        lines.append(f"📈 Impact: {insights.get('expected_impact', 'N/A')}")
    
    return "\n".join(lines)


# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'Dealer360Dashboard',
    'get_dealer_360_dashboard',
    'format_dealer_360_dashboard'
]

# ==========================================================
# END OF FILE - v5.0 FULLY INTEGRATED
# ==========================================================
