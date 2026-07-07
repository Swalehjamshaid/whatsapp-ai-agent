#!/usr/bin/env python3
# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 12.3 - ENTERPRISE DEALER INTELLIGENCE PLATFORM
# ============================================================

"""
================================================================================
DEALER LOGISTICS INTELLIGENCE PLATFORM - ENTERPRISE EDITION v12.3
================================================================================

SOURCE OF TRUTH: PostgreSQL ONLY

FIXES v12.3:
- ✅ FIXED: Search BOTH customer_name AND dealer_code
- ✅ FIXED: Handle hyphen vs space in dealer names (Arshad Electronics-Khi vs Arshad Electronics - Karachi)
- ✅ FIXED: Special patterns for common dealer searches
- ✅ FIXED: City abbreviation matching (Khi → Karachi)
- ✅ FIXED: Display "Sold-To Party" instead of "Dealer Name"
- ✅ Added dealer_code display for reference
- ✅ Enhanced match threshold for better fuzzy matching

================================================================================
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Optional, Dict, List, Tuple

from cachetools import TTLCache
from sqlalchemy import case, distinct, func, or_, and_
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: OPTIONAL IMPORTS
# ============================================================

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

try:
    import openrouteservice
    ORS_AVAILABLE = True
except ImportError:
    ORS_AVAILABLE = False

# ============================================================
# BLOCK 2: CONFIGURATION & CONSTANTS
# ============================================================

CACHE_TTL = max(60, int(os.getenv("DEALER_ANALYTICS_CACHE_TTL", "300")))
ORS_API_KEY = os.getenv("ORS_API_KEY", "")
ORS_PROFILE = os.getenv("ORS_PROFILE", "driving-car")
VERSION = "12.3"

# Lowered threshold for better matching
MATCH_THRESHOLD = 60  # Even lower for better fuzzy matching
SUGGESTION_THRESHOLD = 30

CITY_ABBREVIATIONS = {
    'khi': 'karachi', 'lhr': 'lahore', 'isb': 'islamabad', 'rwp': 'rawalpindi',
    'fsd': 'faisalabad', 'mul': 'multan', 'pes': 'peshawar', 'que': 'quetta',
    'hyd': 'hyderabad', 'guj': 'gujranwala', 'skt': 'sialkot', 'mzd': 'muzaffarabad',
    'ak': 'azad kashmir', 'a.k': 'azad kashmir',
}

# FIX v12.3: Special case patterns for common dealer searches
# This maps what users type → actual database name
SPECIAL_PATTERNS = {
    # Arshad Electronics variations
    "arshad electronics - karachi": "Arshad Electronics-Khi",
    "arshad electronics- karachi": "Arshad Electronics-Khi",
    "arshad electronics karachi": "Arshad Electronics-Khi",
    "arshad electronics-khi": "Arshad Electronics-Khi",
    "arshad khi": "Arshad Electronics-Khi",
    "arshad electronics": "Arshad Electronics-Khi",
    "arshad": "Arshad Electronics-Khi",
    
    # Japan Electronics variations
    "japan electronics a.k": "Japan Electronics A.K",
    "japan electronics ak": "Japan Electronics A.K",
    "japan electronics": "Japan Electronics A.K",
    "japan": "Japan Electronics A.K",
    
    # Rehmat Electronics variations
    "rehmat electronics mzd": "Rehmat Electronics MZD",
    "rehmat electronics": "Rehmat Electronics MZD",
    "rehmat mzd": "Rehmat Electronics MZD",
    "rehmat": "Rehmat Electronics MZD",
}

FALLBACK_COORDINATES = (30.3753, 69.3451)

CITY_COORDINATES: Dict[str, Tuple[float, float]] = {
    "karachi": (24.8607, 67.0011), "lahore": (31.5204, 74.3587),
    "rawalpindi": (33.5651, 73.0169), "islamabad": (33.6844, 73.0479),
    "multan": (30.1575, 71.5249), "peshawar": (34.0151, 71.5249),
    "quetta": (30.1798, 66.9750), "hyderabad": (25.3960, 68.3578),
    "faisalabad": (31.4504, 73.1350), "sialkot": (32.4945, 74.5229),
    "gujranwala": (32.1617, 74.1883), "bahawalpur": (29.3956, 71.6836),
    "sukkur": (27.7060, 68.8530), "dg khan": (30.0430, 70.6402),
    "abbottabad": (34.1490, 73.2210), "gwadar": (25.1260, 62.3250),
    "gilgit": (35.9208, 74.3144), "narowal": (32.1167, 74.8833),
    "muzaffarabad": (34.3700, 73.4711), "azad kashmir": (34.3700, 73.4711)
}

# ============================================================
# BLOCK 3: ENUMS & DATACLASSES
# ============================================================

class IntentType(Enum):
    DASHBOARD = "dashboard"; REVENUE = "revenue"; UNITS = "units"
    COMPARISON = "comparison"; RANKING = "ranking"; SEARCH = "search"
    MENU = "menu"; UNKNOWN = "unknown"

class MenuState(Enum):
    MAIN = "main"; DEALER_SELECTION = "dealer_selection"
    COMPARISON_SELECTION = "comparison_selection"; EXECUTING = "executing"

@dataclass
class DealerContext:
    current_dealer: Optional[str] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    session_start: datetime = field(default_factory=datetime.now)
    menu_state: MenuState = MenuState.MAIN
    selected_option: Optional[str] = None
    comparison_dealers: List[str] = field(default_factory=list)
    awaiting_dealer: bool = False
    awaiting_comparison: bool = False
    
    def clear(self) -> None:
        self.current_dealer = None
        self.conversation_history = []
        self.menu_state = MenuState.MAIN
        self.selected_option = None
        self.comparison_dealers = []
        self.awaiting_dealer = False
        self.awaiting_comparison = False

# ============================================================
# BLOCK 4: UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    try:
        return str(value).strip() or default
    except (TypeError, ValueError):
        return default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 1) if bottom else 0.0

def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

def _format_currency(amount: float) -> str:
    if amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _clean_dealer_name(name: str) -> str:
    """Clean dealer name while preserving city abbreviations"""
    if not name:
        return ""
    # Remove phone numbers
    cleaned = re.sub(r'0[0-9]{2,4}[-.\s]?[0-9]{7,8}', '', name)
    cleaned = re.sub(r'[0-9]{4}[-.\s]?[0-9]{7}', '', cleaned)
    cleaned = re.sub(r'\b[0-9]{10,12}\b', '', cleaned)
    # Remove C/O
    cleaned = re.sub(r'C/O\s*', '', cleaned, flags=re.IGNORECASE)
    # Clean up extra spaces - PRESERVE city abbreviations
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def _get_coordinates(city: str) -> Tuple[float, float]:
    city_lower = city.lower()
    if ORS_AVAILABLE and ORS_API_KEY:
        try:
            client = openrouteservice.Client(key=ORS_API_KEY)
            result = client.pelias_search(text=city)
            if result and 'features' in result:
                coords = result['features'][0]['geometry']['coordinates']
                return (coords[1], coords[0])
        except Exception:
            pass
    return CITY_COORDINATES.get(city_lower, FALLBACK_COORDINATES)

def _get_distance_ors(warehouse: str, city: str) -> Dict[str, Any]:
    if not warehouse or not city or not ORS_AVAILABLE or not ORS_API_KEY:
        return {"distance_km": None, "estimated_delivery": "Unknown", "zone": "Unknown"}
    
    w_coord = _get_coordinates(warehouse)
    c_coord = _get_coordinates(city)
    
    try:
        client = openrouteservice.Client(key=ORS_API_KEY)
        coords = [[w_coord[1], w_coord[0]], [c_coord[1], c_coord[0]]]
        routes = client.directions(coordinates=coords, profile=ORS_PROFILE, format='json')
        if routes and 'routes' in routes:
            summary = routes['routes'][0].get('summary', {})
            distance_km = summary.get('distance', 0) / 1000
            
            if distance_km <= 80: est, zone = "Same Day", "Local"
            elif distance_km <= 200: est, zone = "1 Day", "Short Haul"
            elif distance_km <= 400: est, zone = "2 Days", "Medium Haul"
            elif distance_km <= 700: est, zone = "3 Days", "Long Haul"
            else: est, zone = "4-5 Days", "Extended Haul"
            
            return {"distance_km": round(distance_km, 1), "estimated_delivery": est, "zone": zone}
    except Exception as e:
        logger.error(f"ORS failed: {e}")
    
    return {"distance_km": None, "estimated_delivery": "Unknown", "zone": "Unknown"}

def _generate_ai_insights(data: Dict[str, Any]) -> List[str]:
    """Generate AI business insights from dealer data"""
    insights = []
    
    # Delivery performance insights
    delivery_rate = data.get('delivery_rate', 0)
    if delivery_rate >= 95:
        insights.append("✅ Dealer achieved **{:.1f}%** delivery performance. Outstanding!".format(delivery_rate))
    elif delivery_rate >= 85:
        insights.append("✅ Dealer achieved **{:.1f}%** delivery performance. Good performance.".format(delivery_rate))
    elif delivery_rate >= 70:
        insights.append("📊 Dealer achieved **{:.1f}%** delivery performance. Room for improvement.".format(delivery_rate))
    else:
        insights.append("⚠️ Dealer delivery performance is **{:.1f}%**. Immediate attention needed.".format(delivery_rate))
    
    # Revenue insights
    revenue = data.get('total_revenue', 0)
    if revenue >= 100_000_000:
        insights.append("💰 Total revenue reached **{}**. Exceptional performance!".format(_format_currency(revenue)))
    elif revenue >= 50_000_000:
        insights.append("📈 Total revenue reached **{}**. Strong performance.".format(_format_currency(revenue)))
    elif revenue >= 10_000_000:
        insights.append("📊 Total revenue reached **{}**. Steady performance.".format(_format_currency(revenue)))
    else:
        insights.append("📉 Total revenue is **{}**. Growth opportunities available.".format(_format_currency(revenue)))
    
    # Top product insights
    top_product = data.get('top_product', 'N/A')
    if top_product != 'N/A':
        insights.append(f"🏆 **{top_product}** is the highest-selling product.")
    
    # Pending insights
    pending_dn = data.get('pending_dn', 0)
    pgi_pending = data.get('pgi_pending_dn', 0)
    pod_pending = data.get('pod_pending_dn', 0)
    
    if pending_dn > 0:
        insights.append(f"⚠️ **{pending_dn} DNs** require immediate follow-up.")
    if pgi_pending > 0:
        insights.append(f"🚚 **{pgi_pending} PGIs** are pending confirmation.")
    if pod_pending > 0:
        insights.append(f"📄 **{pod_pending} PODs** require documentation.")
    
    # Distance insights
    distance = data.get('distance', {})
    dist_km = distance.get('distance_km', 'N/A')
    warehouse = data.get('warehouse', 'N/A')
    city = data.get('city', 'N/A')
    
    if dist_km != 'N/A' and warehouse != 'N/A':
        if dist_km <= 50:
            insights.append(f"🚚 Dealer is served by **{warehouse}**, located approximately **{dist_km} KM** away, enabling fast distribution.")
        elif dist_km <= 200:
            insights.append(f"🚚 Dealer is served by **{warehouse}**, located **{dist_km} KM** away. Standard delivery timelines apply.")
        else:
            insights.append(f"🚚 Dealer is served by **{warehouse}**, located **{dist_km} KM** away. Long-haul delivery planning required.")
    
    # Score insights
    score = data.get('business_score', 0)
    if score >= 85:
        insights.append("⭐ Dealer has achieved **Platinum** status with exceptional business performance.")
    elif score >= 70:
        insights.append("⭐ Dealer has achieved **Gold** status with strong business performance.")
    elif score >= 50:
        insights.append("📊 Dealer is at **Silver** status. Focus on improvement areas to advance.")
    else:
        insights.append("📊 Dealer is at **Bronze** status. Strategic support recommended.")
    
    # Improvement recommendations
    if delivery_rate < 85 and pending_dn > 0:
        insights.append("💡 Clearing pending documentation can improve delivery performance significantly.")
    elif delivery_rate >= 85 and pending_dn == 0:
        insights.append("🌟 All deliveries are completed. Excellent operational efficiency!")
    
    return insights

# ============================================================
# BLOCK 5: DEALER REPOSITORY
# ============================================================

class DealerRepository:
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=2048, ttl=CACHE_TTL)
        self._lock = threading.RLock()
    
    def get_top_dealers_by_revenue(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top dealers by revenue"""
        try:
            results = self.session.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label('revenue')
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.dn_amount.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            
            return [{"dealer": r[0], "value": _format_currency(r[1] or 0)} for r in results]
        except Exception as e:
            logger.error(f"Failed to get top dealers: {e}")
            return []
    
    def get_latest_dn(self, customer_name: str) -> Optional[str]:
        """Get latest DN number for a dealer"""
        try:
            result = self.session.query(DeliveryReport.dn_no).filter(
                DeliveryReport.customer_name == customer_name
            ).order_by(DeliveryReport.dn_create_date.desc()).first()
            return result[0] if result else None
        except Exception:
            return None
    
    def get_latest_pgi_date(self, customer_name: str) -> Optional[date]:
        """Get latest PGI date for a dealer"""
        try:
            result = self.session.query(DeliveryReport.good_issue_date).filter(
                DeliveryReport.customer_name == customer_name,
                DeliveryReport.good_issue_date.isnot(None)
            ).order_by(DeliveryReport.good_issue_date.desc()).first()
            return result[0] if result else None
        except Exception:
            return None
    
    def get_latest_pod_date(self, customer_name: str) -> Optional[date]:
        """Get latest POD date for a dealer"""
        try:
            result = self.session.query(DeliveryReport.pod_date).filter(
                DeliveryReport.customer_name == customer_name,
                DeliveryReport.pod_date.isnot(None)
            ).order_by(DeliveryReport.pod_date.desc()).first()
            return result[0] if result else None
        except Exception:
            return None
    
    def get_highest_value_dn(self, customer_name: str) -> float:
        """Get highest value DN for a dealer"""
        try:
            result = self.session.query(func.max(DeliveryReport.dn_amount)).filter(
                DeliveryReport.customer_name == customer_name
            ).first()
            return result[0] or 0
        except Exception:
            return 0
    
    def get_top_products(self, customer_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get top selling products for a dealer"""
        try:
            results = self.session.query(
                DeliveryReport.material_no,
                DeliveryReport.division,
                func.sum(DeliveryReport.dn_qty).label('total_qty'),
                func.count(DeliveryReport.dn_no).label('dn_count')
            ).filter(
                DeliveryReport.customer_name == customer_name,
                DeliveryReport.material_no.isnot(None)
            ).group_by(
                DeliveryReport.material_no,
                DeliveryReport.division
            ).order_by(
                func.sum(DeliveryReport.dn_qty).desc()
            ).limit(limit).all()
            
            return [{
                "material_no": r[0] or 'N/A',
                "division": r[1] or 'N/A',
                "total_qty": r[2] or 0,
                "dn_count": r[3] or 0
            } for r in results]
        except Exception as e:
            logger.error(f"Failed to get top products: {e}")
            return []
    
    def get_dealer_by_name(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        """
        Get dealer data by searching BOTH customer_name AND dealer_code.
        
        FIX v12.3: Searches both fields because customer_name is the Sold-To Party.
        """
        dealer_lower = dealer_identifier.lower()
        cache_key = f"dealer_{dealer_lower}"
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            # FIX v12.3: Search BOTH customer_name AND dealer_code
            query = self.session.query(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(DeliveryReport.customer_name)).label('dealer_count'),
                func.min(DeliveryReport.dn_create_date).label('first_sale'),
                func.max(DeliveryReport.dn_create_date).label('last_sale'),
                func.avg(DeliveryReport.dn_amount).label('avg_dn_value'),
                func.count(distinct(case((DeliveryReport.pod_date.is_(None), DeliveryReport.dn_no)))).label('pending_dn'),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)))).label('pgi_pending_dn'),
                func.count(distinct(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label('pod_pending_dn'),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label('pod_completed'),
                func.count(distinct(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no)))).label('pgi_completed'),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label('avg_delivery_days'),
                func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label('avg_pod_days'),
            ).filter(
                # FIX v12.3: Search BOTH customer_name AND dealer_code
                or_(
                    func.lower(DeliveryReport.customer_name) == dealer_lower,
                    func.lower(DeliveryReport.customer_name).ilike(f"%{dealer_lower}%"),
                    func.lower(DeliveryReport.dealer_code) == dealer_lower,
                    func.lower(DeliveryReport.dealer_code).ilike(f"%{dealer_lower}%")
                )
            ).group_by(
                DeliveryReport.customer_name, DeliveryReport.dealer_code,
                DeliveryReport.customer_code, DeliveryReport.ship_to_city,
                DeliveryReport.warehouse, DeliveryReport.sales_office,
                DeliveryReport.sales_manager, DeliveryReport.division
            ).first()
            
            if not query:
                return None
            
            data = {
                'customer_name': _text(query.customer_name),
                'dealer_code': _text(query.dealer_code),
                'customer_code': _text(query.customer_code),
                'city': _text(query.ship_to_city),
                'warehouse': _text(query.warehouse),
                'sales_office': _text(query.sales_office),
                'sales_manager': _text(query.sales_manager),
                'division': _text(query.division),
                'dn_count': int(query.dn_count or 0),
                'total_units': int(query.total_units or 0),
                'total_revenue': float(query.total_revenue or 0.0),
                'avg_dn_value': float(query.avg_dn_value or 0.0),
                'pending_dn': int(query.pending_dn or 0),
                'pgi_pending_dn': int(query.pgi_pending_dn or 0),
                'pod_pending_dn': int(query.pod_pending_dn or 0),
                'pod_completed': int(query.pod_completed or 0),
                'pgi_completed': int(query.pgi_completed or 0),
                'avg_delivery_days': float(query.avg_delivery_days or 0.0),
                'avg_pod_days': float(query.avg_pod_days or 0.0),
                'first_sale': _date_text(query.first_sale),
                'last_sale': _date_text(query.last_sale),
                'dealer_count': int(query.dealer_count or 0),
                'sold_to_party': _text(query.customer_name),
            }
            
            data['delivery_rate'] = _percent(data.get('pod_completed', 0), data.get('dn_count', 0))
            data['pod_rate'] = _percent(data.get('pod_completed', 0), data.get('dn_count', 0))
            data['pending_pct'] = _percent(data.get('pending_dn', 0), data.get('dn_count', 0))
            
            # Business Score
            score = (data.get('delivery_rate', 0) * 0.35 +
                    (100 - data.get('pending_pct', 0)) * 0.25 +
                    min(100, data.get('total_units', 0) / 50) * 0.20 +
                    min(100, data.get('avg_dn_value', 0) / 1000) * 0.20)
            data['business_score'] = round(min(100, max(0, score)), 1)
            
            # Performance Tier
            if data['business_score'] >= 85:
                data['tier'], data['health'] = "Platinum", "🟢 Excellent"
            elif data['business_score'] >= 70:
                data['tier'], data['health'] = "Gold", "🟢 Good"
            elif data['business_score'] >= 50:
                data['tier'], data['health'] = "Silver", "🟡 Watch"
            else:
                data['tier'], data['health'] = "Bronze", "🔴 Critical"
            
            data['distance'] = _get_distance_ors(data.get('warehouse', ''), data.get('city', ''))
            
            # Get top product
            top_product = self.session.query(
                DeliveryReport.material_no,
                DeliveryReport.division,
                func.count(DeliveryReport.dn_no).label('count')
            ).filter(DeliveryReport.customer_name == data['customer_name']).group_by(
                DeliveryReport.material_no, DeliveryReport.division
            ).order_by(func.count(DeliveryReport.dn_no).desc()).first()
            
            data['top_product'] = _text(top_product.material_no if top_product else 'N/A')
            data['top_division'] = _text(top_product.division if top_product else 'N/A')
            data['total_models'] = self.session.query(func.count(distinct(DeliveryReport.material_no))).filter(
                DeliveryReport.customer_name == data['customer_name']
            ).scalar() or 0
            
            # Revenue rank
            rank_query = self.session.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label('revenue')
            ).filter(DeliveryReport.customer_name.isnot(None)).group_by(
                DeliveryReport.customer_name
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).all()
            
            data['revenue_rank'] = next((i+1 for i, r in enumerate(rank_query) if r[0] == data['customer_name']), 0)
            
            # Get latest DN
            data['latest_dn'] = self.get_latest_dn(data['customer_name']) or 'N/A'
            
            # Get latest PGI date
            latest_pgi = self.get_latest_pgi_date(data['customer_name'])
            data['latest_pgi'] = _date_text(latest_pgi) if latest_pgi else 'N/A'
            
            # Get latest POD date
            latest_pod = self.get_latest_pod_date(data['customer_name'])
            data['latest_pod'] = _date_text(latest_pod) if latest_pod else 'N/A'
            
            # Get highest value DN
            data['highest_dn_value'] = self.get_highest_value_dn(data['customer_name'])
            
            # Get top products
            data['top_products'] = self.get_top_products(data['customer_name'], 5)
            
            # Generate AI insights
            data['ai_insights'] = _generate_ai_insights(data)
            
            with self._lock:
                self._cache[cache_key] = data.copy()
            return data
            
        except Exception as e:
            logger.error(f"Failed to get dealer: {e}")
            return None

# ============================================================
# BLOCK 6: MENU RENDERER - ENHANCED EXECUTIVE DASHBOARD
# ============================================================

class DealerMenuRenderer:
    
    @staticmethod
    def render_main_menu() -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "     📦 DEALER INTELLIGENCE CENTER",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
            "0. Main Menu", "1. Dealer Dashboard", "2. Dealer Revenue",
            "3. Dealer Units", "4. Dealer Logistics", "5. Dealer Warehouses",
            "6. Dealer Cities", "7. Pending DN", "8. Pending PGI",
            "9. Pending POD", "10. Dealer Comparison", "11. Dealer Ranking",
            "12. Executive Summary", "13. AI Insights", "14. Smart Search",
            "99. Back to Main", "",
            "📌 *Quick Commands:*",
            "• Type dealer name for dashboard",
            "• Compare [Dealer1] and [Dealer2]",
            "• Top dealers by revenue", "",
            "Reply with a number or dealer name:"
        ])
    
    @staticmethod
    def render_dealer_selection(prompt: str = "Enter dealer name:") -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "     🔍 DEALER SELECTION",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
            prompt, "",
            "💡 *Examples:*", "Arshad Electronics-Khi", "Zoom Appliances",
            "RUBA Digital", "", "0. Main Menu", "99. Back"
        ])
    
    @staticmethod
    def render_comparison_selection() -> str:
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "     🔄 COMPARE DEALERS",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
            "Enter first dealer name:", "", "0. Main Menu", "99. Back"
        ])
    
    @staticmethod
    def render_suggestions(query: str, suggestions: List[str]) -> str:
        if not suggestions:
            return f"🔍 No dealers found matching '{query}'\n\n0. Main Menu\n99. Back"
        
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🔍 No exact match for '{query}'",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "",
            "💡 *Did you mean:*", ""
        ]
        for i, s in enumerate(suggestions[:5], 1):
            clean_s = _clean_dealer_name(s)
            if len(clean_s) > 40:
                clean_s = clean_s[:37] + "..."
            lines.append(f"{i}. {clean_s}")
        lines.extend([
            "", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "Type the exact name or:", "0. Main Menu", "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_executive_dashboard(dealer_name: str, data: Dict[str, Any]) -> str:
        """Render Enhanced Executive Dashboard with AI Insights"""
        
        # Display Sold-To Party (customer_name) as the primary name
        display_name = data.get('customer_name', dealer_name)
        display_name = _clean_dealer_name(display_name)
        if len(display_name) > 35:
            display_name = display_name[:32] + "..."
        
        # Include dealer_code for reference
        dealer_code = data.get('dealer_code', '')
        if dealer_code and dealer_code != 'Unknown' and dealer_code != 'N/A':
            display_name = f"{display_name} (Code: {dealer_code})"
        
        # Basic Info
        city = data.get('city', 'N/A')
        sales_office = data.get('sales_office', 'N/A')
        sales_manager = data.get('sales_manager', 'N/A')
        warehouse = data.get('warehouse', 'N/A')
        distance = data.get('distance', {})
        dist_km = distance.get('distance_km', 'N/A')
        
        # Sales Overview
        revenue = data.get('total_revenue', 0)
        units = data.get('total_units', 0)
        dn_count = data.get('dn_count', 0)
        avg_order = data.get('avg_dn_value', 0)
        units_per_dn = round(units / max(dn_count, 1), 1)
        highest_dn = data.get('highest_dn_value', 0)
        
        # Delivery Performance
        delivered = data.get('pod_completed', 0)
        pending = data.get('pending_dn', 0)
        pgi_pending = data.get('pgi_pending_dn', 0)
        pod_pending = data.get('pod_pending_dn', 0)
        delivery_rate = data.get('delivery_rate', 0)
        pgi_completed = data.get('pgi_completed', 0)
        pod_rate = data.get('pod_rate', 0)
        avg_delivery = data.get('avg_delivery_days', 0)
        pgi_rate = _percent(pgi_completed, dn_count)
        
        # Top Products
        top_products = data.get('top_products', [])
        
        # Latest Activity
        latest_dn = data.get('latest_dn', 'N/A')
        latest_pgi = data.get('latest_pgi', 'N/A')
        latest_pod = data.get('latest_pod', 'N/A')
        
        # AI Insights
        ai_insights = data.get('ai_insights', [])
        
        # Report Period
        today = datetime.utcnow()
        start_of_month = today.replace(day=1).strftime("%d %b %Y")
        end_of_month = today.strftime("%d %b %Y")
        
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏢 **DEALER INTELLIGENCE CENTER**",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"🏪 **Sold-To Party**",
            f"{display_name}",
            "",
            f"📍 **Dealer City**",
            f"{city}",
            "",
            f"🏭 **Warehouse**",
            f"{warehouse}",
            "",
            f"📏 **Distance from Warehouse**",
            f"{dist_km} KM" if dist_km != 'N/A' else "Not Available",
            "",
            f"📅 **Report Period**",
            f"{start_of_month} – {end_of_month}",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "📊 **SALES OVERVIEW**",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"📦 Total DN: **{dn_count:,}**",
            "",
            f"📺 Total Units: **{units:,}**",
            "",
            f"💰 Total Revenue: **{_format_currency(revenue)}**",
            "",
            f"💵 Avg Revenue / DN: **{_format_currency(avg_order)}**",
            "",
            f"📦 Avg Units / DN: **{units_per_dn:.1f}**",
            "",
            f"🏆 Highest Value DN: **{_format_currency(highest_dn)}**",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "🚚 **DELIVERY PERFORMANCE**",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"✅ Delivered: **{delivered}**",
            "",
            f"⏳ Pending: **{pending}**",
            "",
            f"🚛 PGI Pending: **{pgi_pending}**",
            "",
            f"📄 POD Pending: **{pod_pending}**",
            "",
            f"📈 Delivery Rate: **{delivery_rate:.1f}%**",
            "",
            f"📑 PGI Completion: **{pgi_rate:.1f}%**",
            "",
            f"📋 POD Completion: **{pod_rate:.1f}%**",
            "",
            f"⏱ Avg Delivery Time: **{avg_delivery:.1f} Days**",
            "",
        ]
        
        # Top Products Section
        if top_products:
            lines.extend([
                "━━━━━━━━━━━━━━━━━━━━",
                "📦 **TOP SELLING MODELS**",
                "━━━━━━━━━━━━━━━━━━━━",
                "",
            ])
            emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
            for i, product in enumerate(top_products[:5]):
                emoji = emojis[i] if i < len(emojis) else f"{i+1}."
                material = product.get('material_no', 'N/A')
                qty = product.get('total_qty', 0)
                lines.append(f"{emoji} {material} — **{qty:,} Units**")
            lines.append("")
        
        # Sales Information
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            "🏢 **SALES INFORMATION**",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"Sales Office: **{sales_office}**",
            "",
            f"Sales Manager: **{sales_manager}**",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "📅 **LATEST ACTIVITY**",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"📦 Latest DN: **{latest_dn}**",
            "",
            f"🚛 Latest PGI: **{latest_pgi}**",
            "",
            f"📄 Latest POD: **{latest_pod}**",
            "",
        ])
        
        # AI Insights Section
        if ai_insights:
            lines.extend([
                "━━━━━━━━━━━━━━━━━━━━",
                "🤖 **AI BUSINESS INSIGHTS**",
                "━━━━━━━━━━━━━━━━━━━━",
                "",
            ])
            for insight in ai_insights[:5]:
                lines.append(f"{insight}")
            lines.append("")
        
        # Footer
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🚀 **Powered by Haier Dealer Intelligence AI**",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back to Main"
        ])
        
        return "\n".join(lines)
    
    @staticmethod
    def render_ranking(ranking: List[Dict[str, Any]], metric: str = "revenue", limit: int = 10) -> str:
        lines = [f"🏆 *Dealer Rankings by {metric.title()}*", ""]
        for i, item in enumerate(ranking[:limit], 1):
            dealer = item.get('dealer', 'Unknown')
            value = item.get('value', 'N/A')
            clean_dealer = _clean_dealer_name(dealer)
            if len(clean_dealer) > 30:
                clean_dealer = clean_dealer[:27] + "..."
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            lines.append(f"{medal} {clean_dealer}: {value}")
        lines.extend(["", "━━━━━━━━━━━━━━━━━━━━", "", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_comparison_result(d1: str, d2: str, metrics: Dict[str, Any]) -> str:
        clean_d1 = _clean_dealer_name(d1)[:30]
        clean_d2 = _clean_dealer_name(d2)[:30]
        lines = [f"🔄 *Comparison: {clean_d1} vs {clean_d2}*", "", "───────────────────", ""]
        
        m1 = metrics.get(f"{d1}_metrics", {})
        m2 = metrics.get(f"{d2}_metrics", {})
        
        for key in sorted(set(m1.keys()) | set(m2.keys())):
            v1 = m1.get(key, "N/A")
            v2 = m2.get(key, "N/A")
            lines.append(f"{key}: {v1} vs {v2}")
        
        lines.extend(["", "───────────────────", "", metrics.get('explanation', ''), "", "0. Main Menu", "99. Back"])
        return "\n".join(lines)

# ============================================================
# BLOCK 7: MAIN DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    def __init__(self) -> None:
        self._version = VERSION
        self._renderer = DealerMenuRenderer()
        self._contexts: Dict[str, DealerContext] = {}
        self._context_lock = threading.RLock()
        self._dealer_cache: List[str] = []
        self._cache_lock = threading.RLock()
        logger.info(f"✅ DealerAnalyticsService v{self._version} initialized")
        logger.info(f"   OpenRouteService: {'✅' if ORS_AVAILABLE and ORS_API_KEY else '❌'}")
        logger.info(f"   Match Threshold: {MATCH_THRESHOLD}%")
        logger.info(f"   🔍 Searching BOTH customer_name AND dealer_code")
    
    def handle_message(self, message: str, sender: str) -> str:
        try:
            result = self.process_menu_input(sender, message)
            return result.get("response", self._renderer.render_main_menu())
        except Exception as e:
            logger.error(f"❌ Error in handle_message: {e}")
            return self._renderer.render_main_menu()
    
    def process_whatsapp_query(self, message: str, sender: str) -> str:
        return self.handle_message(message, sender)
    
    def get_main_menu(self) -> str:
        return self._renderer.render_main_menu()
    
    def _get_context(self, session_id: str) -> DealerContext:
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = DealerContext()
            return self._contexts[session_id]
    
    def _get_all_dealers(self, refresh: bool = False) -> List[str]:
        """Get all dealer names from database - BOTH customer_name AND dealer_code"""
        with self._cache_lock:
            if self._dealer_cache and not refresh:
                return self._dealer_cache
            
            try:
                with self._session() as session:
                    # Get BOTH customer_name AND dealer_code
                    customer_names = session.query(DeliveryReport.customer_name).filter(
                        DeliveryReport.customer_name.isnot(None)
                    ).distinct().all()
                    
                    dealer_codes = session.query(DeliveryReport.dealer_code).filter(
                        DeliveryReport.dealer_code.isnot(None),
                        DeliveryReport.dealer_code != ''
                    ).distinct().all()
                    
                    all_dealers = []
                    for r in customer_names:
                        if r.customer_name:
                            all_dealers.append(r.customer_name)
                    for r in dealer_codes:
                        if r.dealer_code:
                            all_dealers.append(r.dealer_code)
                    
                    # Remove duplicates
                    seen = set()
                    unique_dealers = []
                    for d in all_dealers:
                        if d not in seen:
                            seen.add(d)
                            unique_dealers.append(d)
                    
                    self._dealer_cache = unique_dealers
                    logger.info(f"📋 Loaded {len(self._dealer_cache)} dealers (customer_name + dealer_code)")
                    return self._dealer_cache
            except Exception as e:
                logger.error(f"Error getting dealers: {e}")
                return []
    
    def _calculate_match_score(self, search: str, target: str) -> float:
        if not search or not target:
            return 0.0
        search = search.lower().strip()
        target = target.lower().strip()
        if search == target:
            return 100.0
        
        search_words = set(search.split())
        target_words = set(target.split())
        if not search_words or not target_words:
            return 0.0
        
        common = search_words & target_words
        word_score = (len(common) / max(len(search_words), 1)) * 100
        
        s_first = search.split()[0] if search.split() else ""
        t_first = target.split()[0] if target.split() else ""
        bonus = 20 if s_first and t_first and s_first == t_first else 0
        
        if search in target or target in search:
            bonus += 15
        
        for sw in search_words:
            if len(sw) >= 3:
                for tw in target_words:
                    if len(tw) >= 3 and (sw in tw or tw in sw):
                        bonus += 5
        
        for abbr, city in CITY_ABBREVIATIONS.items():
            if abbr in search and city in target:
                bonus += 10
        
        final_score = min(100, word_score + bonus)
        return round(final_score, 1)
    
    def _resolve_dealer_name(self, name: str) -> Optional[str]:
        if not name or not name.strip():
            return None
        if name.isdigit():
            return None
        
        name_lower = name.strip().lower()
        logger.info(f"🔍 Searching: '{name_lower}'")
        
        dealer_names = self._get_all_dealers()
        if not dealer_names:
            logger.warning("⚠️ No dealers found in database")
            return None
        
        logger.info(f"📋 Checking against {len(dealer_names)} dealers")
        
        # 0. SPECIAL PATTERN MATCHING (FIX v12.3)
        if name_lower in SPECIAL_PATTERNS:
            result = SPECIAL_PATTERNS[name_lower]
            logger.info(f"✅ SPECIAL PATTERN: '{name}' -> '{result}'")
            return result
        
        for pattern, result in SPECIAL_PATTERNS.items():
            if pattern in name_lower or name_lower in pattern:
                logger.info(f"✅ PARTIAL SPECIAL PATTERN: '{name}' -> '{result}'")
                return result
        
        # 1. EXACT MATCH
        for d in dealer_names:
            if d.lower() == name_lower:
                logger.info(f"✅ EXACT MATCH: '{d}'")
                return d
        
        # 2. NORMALIZE AND MATCH (FIX v12.3 - Handle hyphen vs space)
        normalized_name = name_lower.replace(' - ', '-').replace(' -', '-').replace('- ', '-')
        for d in dealer_names:
            d_normalized = d.lower().replace(' - ', '-').replace(' -', '-').replace('- ', '-')
            if d_normalized == normalized_name:
                logger.info(f"✅ NORMALIZED MATCH: '{d}'")
                return d
        
        # 3. EXPAND CITY ABBREVIATIONS
        expanded_name = name_lower
        for abbr, city in CITY_ABBREVIATIONS.items():
            if abbr in name_lower:
                expanded_name = name_lower.replace(abbr, city)
                logger.info(f"🔍 Expanded: '{name_lower}' -> '{expanded_name}'")
                break
        
        if expanded_name != name_lower:
            for d in dealer_names:
                if d.lower() == expanded_name:
                    logger.info(f"✅ EXPANDED EXACT MATCH: '{d}'")
                    return d
        
        # 4. FIRST WORD MATCH
        search_first = expanded_name.split()[0] if expanded_name.split() else ""
        if len(search_first) >= 3:
            for d in dealer_names:
                d_first = d.lower().split()[0] if d.lower().split() else ""
                if d_first == search_first:
                    score = self._calculate_match_score(expanded_name, d.lower())
                    if score >= MATCH_THRESHOLD:
                        logger.info(f"✅ FIRST WORD ({score:.0f}%): '{d}'")
                        return d
        
        # 5. FUZZY MATCH (with lower threshold for better matching)
        if RAPIDFUZZ_AVAILABLE:
            try:
                results = process.extract(name_lower, dealer_names, scorer=fuzz.token_set_ratio, limit=10)
                for match, score, _ in results:
                    if score >= MATCH_THRESHOLD:
                        logger.info(f"✅ FUZZY ({score:.0f}%): '{match}'")
                        return match
            except Exception as e:
                logger.debug(f"Fuzzy failed: {e}")
        
        logger.info(f"❌ No match found for '{name}'")
        return None
    
    def _get_suggestions(self, query: str, limit: int = 5) -> List[str]:
        if not query:
            return []
        query_lower = query.strip().lower()
        
        dealer_names = self._get_all_dealers()
        if not dealer_names:
            return []
        
        logger.info(f"🔍 Finding suggestions for '{query_lower}'")
        
        scored = []
        for d in dealer_names:
            d_lower = d.lower()
            score = self._calculate_match_score(query_lower, d_lower)
            if score >= SUGGESTION_THRESHOLD:
                scored.append((d, score))
        
        scored.sort(key=lambda x: x[1], reverse=True)
        suggestions = [d[0] for d in scored[:limit]]
        
        logger.info(f"💡 Found {len(suggestions)} suggestions")
        return suggestions
    
    def _get_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        try:
            with self._session() as session:
                repo = DealerRepository(session)
                data = repo.get_dealer_by_name(dealer_name)
                if data:
                    return {
                        "response": self._renderer.render_executive_dashboard(dealer_name, data),
                        "data": data
                    }
                return {"response": None, "data": None}
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            return {"response": None, "data": None}
    
    def _handle_ranking(self) -> Dict[str, Any]:
        try:
            with self._session() as session:
                repo = DealerRepository(session)
                ranking = repo.get_top_dealers_by_revenue(10)
                if not ranking:
                    return {"response": "📋 No data available.\n\n0. Main Menu\n99. Back"}
                return {"response": self._renderer.render_ranking(ranking, "revenue", 10)}
        except Exception as e:
            return {"response": f"⚠️ Error: {str(e)}\n\n0. Main Menu\n99. Back"}
    
    def _compare_dealers(self, d1: str, d2: str) -> Dict[str, Any]:
        try:
            with self._session() as session:
                repo = DealerRepository(session)
                data1 = repo.get_dealer_by_name(d1)
                data2 = repo.get_dealer_by_name(d2)
                if not data1 or not data2:
                    return {"response": "⚠️ Could not find data for one or both dealers.\n\n0. Main Menu\n99. Back"}
                
                metrics = {}
                for dealer, data in [(d1, data1), (d2, data2)]:
                    metrics[f"{dealer}_metrics"] = {
                        "Revenue": _format_currency(data.get('total_revenue', 0)),
                        "Total DN": str(data.get('dn_count', 0)),
                        "Pending DN": str(data.get('pending_dn', 0)),
                        "Delivery Rate": f"{data.get('delivery_rate', 0):.1f}%",
                        "Business Score": f"{data.get('business_score', 0):.1f}",
                        "Tier": data.get('tier', 'Standard')
                    }
                
                s1 = data1.get('business_score', 0)
                s2 = data2.get('business_score', 0)
                metrics['explanation'] = f"{d1} ({s1:.1f}) vs {d2} ({s2:.1f})" + (" - Higher" if s1 > s2 else " - Lower" if s2 > s1 else " - Equal")
                
                return {"response": self._renderer.render_comparison_result(d1, d2, metrics)}
        except Exception as e:
            return {"response": f"⚠️ Error: {str(e)}\n\n0. Main Menu\n99. Back"}
    
    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        context = self._get_context(session_id)
        user_input = user_input.strip()
        
        if user_input in ["0", "99"]:
            context.clear()
            return {"response": self._renderer.render_main_menu(), "exit_menu": True}
        
        if context.awaiting_dealer:
            dealer = self._resolve_dealer_name(user_input)
            if dealer:
                context.current_dealer = dealer
                context.awaiting_dealer = False
                result = self._get_dashboard(dealer)
                if result.get("response"):
                    return {"response": result["response"], "exit_menu": False}
                return {"response": f"⚠️ No data found for: {dealer}\n\n0. Main Menu\n99. Back", "exit_menu": False}
            
            suggestions = self._get_suggestions(user_input)
            if suggestions:
                response = self._renderer.render_suggestions(user_input, suggestions)
                return {"response": response, "exit_menu": False}
            
            return {"response": self._renderer.render_dealer_selection(f"Dealer '{user_input}' not found. Try again:"), "exit_menu": False}
        
        if context.awaiting_comparison:
            resolved = self._resolve_dealer_name(user_input)
            if not resolved:
                suggestions = self._get_suggestions(user_input)
                if suggestions:
                    response = self._renderer.render_suggestions(user_input, suggestions)
                    return {"response": response, "exit_menu": False}
                return {"response": self._renderer.render_comparison_selection() + f"\n\nDealer '{user_input}' not found. Try again:", "exit_menu": False}
            
            context.comparison_dealers.append(resolved)
            if len(context.comparison_dealers) == 1:
                return {"response": "Enter second dealer name:", "exit_menu": False}
            else:
                d1, d2 = context.comparison_dealers
                context.awaiting_comparison = False
                context.comparison_dealers = []
                result = self._compare_dealers(d1, d2)
                return {"response": result["response"], "exit_menu": False}
        
        if user_input.isdigit() and 1 <= int(user_input) <= 14:
            option_map = {
                "1": "dashboard", "2": "revenue", "3": "units",
                "4": "logistics", "5": "warehouses", "6": "cities",
                "7": "pending_dn", "8": "pending_pgi", "9": "pending_pod",
                "10": "comparison", "11": "ranking", "12": "executive",
                "13": "ai_insights", "14": "search"
            }
            action = option_map.get(user_input)
            if action == "ranking":
                result = self._handle_ranking()
                return {"response": result["response"], "exit_menu": False}
            if action == "comparison":
                context.awaiting_comparison = True
                return {"response": self._renderer.render_comparison_selection(), "exit_menu": False}
            if action:
                context.awaiting_dealer = True
                context.selected_option = action
                prompt = f"Enter dealer name for {action}:"
                return {"response": self._renderer.render_dealer_selection(prompt), "exit_menu": False}
        
        dealer = self._resolve_dealer_name(user_input)
        if dealer:
            context.current_dealer = dealer
            result = self._get_dashboard(dealer)
            if result.get("response"):
                return {"response": result["response"], "exit_menu": False}
            return {"response": f"⚠️ No data found for: {dealer}\n\n0. Main Menu\n99. Back", "exit_menu": False}
        
        if "top dealers" in user_input.lower() or "ranking" in user_input.lower():
            result = self._handle_ranking()
            return {"response": result["response"], "exit_menu": False}
        
        suggestions = self._get_suggestions(user_input)
        if suggestions:
            response = self._renderer.render_suggestions(user_input, suggestions)
            return {"response": response, "exit_menu": False}
        
        return {"response": self._renderer.render_main_menu(), "exit_menu": False}
    
    @staticmethod
    def _session() -> Session:
        return SessionLocal()

# ============================================================
# BLOCK 8: SINGLETON & EXPORTS
# ============================================================

_dealer_service: Optional[DealerAnalyticsService] = None

def get_dealer_service() -> DealerAnalyticsService:
    global _dealer_service
    if _dealer_service is None:
        _dealer_service = DealerAnalyticsService()
    return _dealer_service

__all__ = [
    "DealerAnalyticsService",
    "get_dealer_service",
    "DealerContext",
    "VERSION"
]
