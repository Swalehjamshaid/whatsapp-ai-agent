#!/usr/bin/env python3
# ============================================================
# FILE: whatsapp-ai-agent-demo/app/services/dealer_search_service.py
# VERSION: 2.0 - FINAL POSTGRESQL INTEGRATION
# ============================================================

"""
================================================================================
DEALER SEARCH SERVICE - POSTGRESQL INTEGRATION
================================================================================

DATABASE: delivery_reports
PRIMARY DEALER COLUMN: customer_name

This service searches for dealers using customer_name, dealer_code, and customer_code.

================================================================================
"""

import logging
import re
from typing import Optional, Dict, List, Any, Union
from datetime import datetime, date
from sqlalchemy import func, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

EXIT_SIGNAL = "__EXIT__"
FUZZY_THRESHOLD = 70

# ============================================================
# DATABASE IMPORTS
# ============================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    DB_AVAILABLE = True
    logger.info("✅ PostgreSQL connected")
except ImportError as e:
    DB_AVAILABLE = False
    logger.error(f"❌ PostgreSQL connection failed: {e}")

# ============================================================
# RAPIDFUZZ (optional)
# ============================================================

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
    logger.info("✅ RapidFuzz loaded")
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("⚠️ RapidFuzz not available - fuzzy matching disabled")

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

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

def format_currency(amount: float) -> str:
    if amount is None or amount == 0:
        return "PKR 0.00"
    if amount >= 1_000_000_000:
        return f"PKR {amount/1_000_000_000:,.2f} Billion"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:,.2f} Million"
    else:
        return f"PKR {amount:,.2f}"

def format_number(num: Union[int, float]) -> str:
    if num is None:
        return "0"
    return f"{int(num):,}"

def clean_text(text: str) -> str:
    """Clean text for matching - lowercase, strip, remove special chars"""
    if not text:
        return ""
    text = re.sub(r'[^a-zA-Z0-9\s-]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip().lower()

def normalize_no_spaces(text: str) -> str:
    """Remove all spaces"""
    if not text:
        return ""
    return re.sub(r'\s+', '', text).lower()

# ============================================================
# SAMPLE DATA (Fallback - for testing when DB unavailable)
# ============================================================

SAMPLE_DEALERS = {
    "arshad electronics-khi": {
        "name": "Arshad Electronics-Khi",
        "dealer_code": "DEAL_ARSHAD_ELECTRON",
        "customer_code": "CUST_ARSHAD_ELECTRON",
        "sales_office": "Karachi Office",
        "sales_manager": "Traditional Channel",
        "division": "Washing Machine",
        "warehouse": "Karachi",
        "warehouse_code": "KHI",
        "city": "Karachi",
        "delivery_location": "Karachi",
        "revenue": 738427.00,
        "units": 29,
        "total_dn": 4,
        "delivered_dn": 4,
        "pending_dn": 0,
        "pending_pgi": 0,
        "pending_pod": 0,
        "delivery_pct": 100.0,
        "pgi_pct": 100.0,
        "pod_pct": 100.0,
        "avg_delivery_days": 0,
        "avg_pod_days": 8.0,
        "top_product": "HWM 150-826S6 GC",
        "top_model": "HWM 150-826S6 GC",
        "product_count": 6,
        "material_count": 6,
        "warehouses_used": ["Karachi"],
        "cities_served": ["Karachi"],
        "latest_dn": "09-Jun-2026",
        "latest_pgi": "09-Jun-2026",
        "latest_pod": "19-Jun-2026",
        "business_score": 85.0,
        "insights": [
            "💰 Revenue: PKR 0.74 Million",
            "✅ 100% delivery success rate"
        ],
        "recommendations": [
            "📊 Monitor performance metrics",
            "📈 Review delivery efficiency"
        ]
    }
}

# ============================================================
# DEALER SEARCH ENGINE
# ============================================================

class DealerSearchEngine:
    """
    Enterprise Dealer Search Engine
    
    Searches PostgreSQL delivery_reports table using customer_name
    """
    
    _instance: Optional["DealerSearchEngine"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self._initialized = True
        self._version = "2.0"
        self._db_available = DB_AVAILABLE
        
        # Cache for quick lookups
        self._dealer_cache: Dict[str, Dict] = {}
        self._dealer_names: List[str] = []
        
        # Load dealers on startup
        self._load_dealers()
        
        logger.info("=" * 70)
        logger.info("🚀 DEALER SEARCH ENGINE v2.0")
        logger.info(f"   🗄️  PostgreSQL: {'✅ Connected' if self._db_available else '⚠️ Fallback'}")
        logger.info(f"   📚 Dealers Loaded: {len(self._dealer_cache)}")
        logger.info("=" * 70)
    
    def _get_session(self) -> Optional[Session]:
        """Get database session"""
        if not self._db_available:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"❌ Session error: {e}")
            return None
    
    def _load_dealers(self):
        """Load all dealers from PostgreSQL delivery_reports table"""
        if not self._db_available:
            self._load_sample_dealers()
            return
        
        session = self._get_session()
        if not session:
            self._load_sample_dealers()
            return
        
        try:
            # Get all distinct customer_names (dealers) from delivery_reports
            results = session.query(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != '',
                # Filter out non-dealer records
                ~DeliveryReport.customer_name.like('PK%'),
                ~DeliveryReport.customer_name.like('%-prepaid-%'),
                ~DeliveryReport.customer_name.like('%Faiq%'),
                ~DeliveryReport.customer_name.like('%Alam%'),
                # dealer_code should exist and look like a dealer
                DeliveryReport.dealer_code.isnot(None),
                DeliveryReport.dealer_code != ''
            ).distinct().all()
            
            session.close()
            
            for row in results:
                name = _text(row.customer_name)
                if name and name != "N/A" and len(name) > 2:
                    # Store in cache with multiple keys for fast lookup
                    key = clean_text(name)
                    self._dealer_cache[key] = {
                        'name': name,
                        'dealer_code': _text(row.dealer_code),
                        'customer_code': _text(row.customer_code)
                    }
                    if name not in self._dealer_names:
                        self._dealer_names.append(name)
            
            logger.info(f"   ✅ Loaded {len(self._dealer_cache)} dealers from PostgreSQL")
            
        except Exception as e:
            logger.error(f"❌ Failed to load dealers: {e}")
            if session:
                session.close()
            self._load_sample_dealers()
    
    def _load_sample_dealers(self):
        """Load sample dealers for fallback"""
        for key, data in SAMPLE_DEALERS.items():
            self._dealer_cache[key] = {
                'name': data['name'],
                'dealer_code': data['dealer_code'],
                'customer_code': data['customer_code']
            }
            if data['name'] not in self._dealer_names:
                self._dealer_names.append(data['name'])
        
        logger.info(f"   📚 Loaded {len(self._dealer_cache)} sample dealers")
    
    # ============================================================
    # WHATSAPP ENTRY POINT
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        MAIN ENTRY POINT - Called by AIProviderService
        
        Args:
            message: User's input (Dealer Name, Code, etc.)
            sender: User identifier
            
        Returns:
            Dashboard, error message, or EXIT_SIGNAL
        """
        try:
            logger.info(f"📨 Dealer Search: '{message}' from {sender}")
            
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
                return result['dashboard']
            else:
                logger.info(f"❌ Dealer not found: {message_clean}")
                
                if result.get('suggestions'):
                    suggestion_lines = ["❌ Dealer not found.", "", "💡 Did you mean:"]
                    for i, name in enumerate(result['suggestions'][:5], 1):
                        suggestion_lines.append(f"{i}. {name}")
                    suggestion_lines.append("")
                    suggestion_lines.append("Type a number or dealer name, or '99' to exit")
                    return "\n".join(suggestion_lines)
                else:
                    return "\n".join([
                        "❌ Dealer not found.",
                        "",
                        "Please try a different name.",
                        "",
                        "Type '99' to exit"
                    ])
            
        except Exception as e:
            logger.error(f"❌ process_whatsapp_query error: {e}", exc_info=True)
            return f"⚠️ An error occurred: {str(e)[:200]}\n\nPlease type '99' to exit."
    
    # ============================================================
    # WELCOME MESSAGE
    # ============================================================
    
    def get_welcome_message(self) -> str:
        """Display welcome message"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏢 DEALER SEARCH",
            "",
            "Please write the Dealer Name.",
            "",
            "📝 Examples:",
            "• Arshad Electronics-Khi",
            "• Zoom Appliances",
            "• RUBA Digital",
            "• Metro Electronics",
            "• Friends Electronics",
            "• Al Madina Electronics",
            "",
            "✅ Supported Search:",
            "✓ Dealer Name (customer_name)",
            "✓ Dealer Code (dealer_code)",
            "✓ Customer Code (customer_code)",
            "✓ Partial Name",
            "✓ Alias",
            "✓ Fuzzy Search (70%)",
            "",
            "99️⃣ Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    # ============================================================
    # DEALER SEARCH ENGINE
    # ============================================================
    
    def search_dealer(self, query: str) -> Dict[str, Any]:
        """
        Search for dealer using multi-stage matching
        
        Search Priority:
            1. customer_name (exact)
            2. dealer_code
            3. customer_code
            4. Partial matches
            5. Fuzzy matches
        """
        if not query or not query.strip():
            return {
                'success': False,
                'message': "Please enter a Dealer Name."
            }
        
        query_clean = query.strip()
        query_cleaned = clean_text(query_clean)
        
        logger.info(f"🔍 Searching: '{query_clean}'")
        
        # ============================================================
        # STAGE 1: EXACT MATCH on customer_name
        # ============================================================
        result = self._exact_match(query_cleaned)
        if result:
            logger.info(f"   ✅ Exact match: {result['name']} (100%)")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # STAGE 2: IGNORE CASE
        # ============================================================
        result = self._case_insensitive_match(query_cleaned)
        if result:
            logger.info(f"   ✅ Case insensitive match: {result['name']}")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # STAGE 3: IGNORE SPACES
        # ============================================================
        result = self._space_insensitive_match(query_cleaned)
        if result:
            logger.info(f"   ✅ Space insensitive match: {result['name']}")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # STAGE 4: IGNORE SYMBOLS
        # ============================================================
        result = self._symbol_insensitive_match(query_cleaned)
        if result:
            logger.info(f"   ✅ Symbol insensitive match: {result['name']}")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # STAGE 5: CONTAINS MATCH
        # ============================================================
        result = self._contains_match(query_cleaned)
        if result:
            logger.info(f"   ✅ Contains match: {result['name']}")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # STAGE 6: STARTS WITH
        # ============================================================
        result = self._starts_with_match(query_cleaned)
        if result:
            logger.info(f"   ✅ Starts with match: {result['name']}")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # STAGE 7: ENDS WITH
        # ============================================================
        result = self._ends_with_match(query_cleaned)
        if result:
            logger.info(f"   ✅ Ends with match: {result['name']}")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # STAGE 8: WORD MATCH
        # ============================================================
        result = self._word_match(query_cleaned)
        if result:
            logger.info(f"   ✅ Word match: {result['name']}")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # STAGE 9: ALIAS MATCH
        # ============================================================
        result = self._alias_match(query_cleaned)
        if result:
            logger.info(f"   ✅ Alias match: {result['name']}")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # STAGE 10: DEALER CODE MATCH (dealer_code)
        # ============================================================
        result = self._dealer_code_match(query_clean)
        if result:
            logger.info(f"   ✅ Dealer code match: {result['name']} (100%)")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # STAGE 11: CUSTOMER CODE MATCH (customer_code)
        # ============================================================
        result = self._customer_code_match(query_clean)
        if result:
            logger.info(f"   ✅ Customer code match: {result['name']} (100%)")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # STAGE 12: RAPIDFUZZ MATCH (70% threshold)
        # ============================================================
        result = self._fuzzy_match(query_cleaned)
        if result:
            logger.info(f"   ✅ Fuzzy match: {result['name']} ({result['score']:.0f}%)")
            return self._get_dealer_dashboard(result['name'])
        
        # ============================================================
        # NO MATCH - Get suggestions
        # ============================================================
        logger.info(f"   ❌ No match found")
        suggestions = self._get_suggestions(query_cleaned)
        
        if suggestions:
            return {
                'success': False,
                'message': "Dealer not found.",
                'suggestions': suggestions
            }
        
        return {
            'success': False,
            'message': "Dealer not found. Please try a different name."
        }
    
    # ============================================================
    # MATCHING METHODS
    # ============================================================
    
    def _exact_match(self, query: str):
        """Exact match on customer_name"""
        for key, data in self._dealer_cache.items():
            if key == query:
                return data
        return None
    
    def _case_insensitive_match(self, query: str):
        """Case insensitive match on customer_name"""
        for key, data in self._dealer_cache.items():
            if key == query:
                return data
        return None
    
    def _space_insensitive_match(self, query: str):
        """Ignore spaces in customer_name"""
        query_no_space = normalize_no_spaces(query)
        for key, data in self._dealer_cache.items():
            if normalize_no_spaces(key) == query_no_space:
                return data
        return None
    
    def _symbol_insensitive_match(self, query: str):
        """Ignore symbols in customer_name"""
        # Already cleaned in normalize_text
        for key, data in self._dealer_cache.items():
            # Compare cleaned versions
            if clean_text(key) == query:
                return data
        return None
    
    def _contains_match(self, query: str):
        """Contains match - query is part of customer_name"""
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            if query in key:
                score = len(query) / len(key)
                if score > best_score:
                    best_score = score
                    best_match = data
        
        if best_match and best_score > 0.3:
            return best_match
        return None
    
    def _starts_with_match(self, query: str):
        """Starts with match on customer_name"""
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            if key.startswith(query):
                score = len(query) / len(key)
                if score > best_score:
                    best_score = score
                    best_match = data
        
        if best_match and best_score > 0.3:
            return best_match
        return None
    
    def _ends_with_match(self, query: str):
        """Ends with match on customer_name"""
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            if key.endswith(query):
                score = len(query) / len(key)
                if score > best_score:
                    best_score = score
                    best_match = data
        
        if best_match and best_score > 0.3:
            return best_match
        return None
    
    def _word_match(self, query: str):
        """Word match on customer_name"""
        query_words = set(query.split())
        if len(query_words) < 1:
            return None
        
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            key_words = set(key.split())
            common = query_words & key_words
            if common:
                score = len(common) / len(query_words)
                if score > best_score:
                    best_score = score
                    best_match = data
        
        if best_match and best_score > 0.4:
            return best_match
        return None
    
    def _alias_match(self, query: str):
        """Alias match - partial of customer_name"""
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            # Check if any part of name matches
            parts = key.split()
            for part in parts:
                if len(part) >= 3 and part in query:
                    score = len(part) / len(query) if len(query) > 0 else 0
                    if score > best_score:
                        best_score = score
                        best_match = data
                elif len(part) >= 3 and query in part:
                    score = len(query) / len(part)
                    if score > best_score:
                        best_score = score
                        best_match = data
        
        if best_match and best_score > 0.3:
            return best_match
        return None
    
    def _dealer_code_match(self, query: str):
        """Match by dealer_code column"""
        query_clean = query.strip().upper()
        
        for key, data in self._dealer_cache.items():
            dealer_code = data.get('dealer_code', '').upper()
            if dealer_code and dealer_code == query_clean:
                return data
        return None
    
    def _customer_code_match(self, query: str):
        """Match by customer_code column"""
        query_clean = query.strip().upper()
        
        for key, data in self._dealer_cache.items():
            customer_code = data.get('customer_code', '').upper()
            if customer_code and customer_code == query_clean:
                return data
        return None
    
    def _fuzzy_match(self, query: str):
        """Fuzzy match using RapidFuzz (70% threshold)"""
        if not RAPIDFUZZ_AVAILABLE:
            return None
        
        best_match = None
        best_score = 0.0
        
        for key, data in self._dealer_cache.items():
            score = fuzz.WRatio(query, key)
            if score > best_score and score >= FUZZY_THRESHOLD:
                best_score = score
                best_match = data
        
        if best_match:
            return {
                'name': best_match['name'],
                'dealer_code': best_match.get('dealer_code', ''),
                'customer_code': best_match.get('customer_code', ''),
                'score': best_score
            }
        return None
    
    def _get_suggestions(self, query: str, limit: int = 5) -> List[str]:
        """Get suggestions for no match"""
        suggestions = []
        
        if RAPIDFUZZ_AVAILABLE:
            scored = []
            for key, data in self._dealer_cache.items():
                score = fuzz.WRatio(query, key)
                if score > 50:
                    scored.append((score, data['name']))
            
            scored.sort(key=lambda x: x[0], reverse=True)
            
            for score, name in scored[:limit]:
                if name not in suggestions:
                    suggestions.append(name)
        else:
            # Fallback to contains match
            for key, data in self._dealer_cache.items():
                if query in key or key in query:
                    if data['name'] not in suggestions:
                        suggestions.append(data['name'])
                        if len(suggestions) >= limit:
                            break
        
        return suggestions
    
    # ============================================================
    # DASHBOARD GENERATION
    # ============================================================
    
    def _get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Get full dealer dashboard from PostgreSQL"""
        if self._db_available:
            result = self._query_dealer_dashboard(dealer_name)
            if result:
                return result
        
        # Fallback to sample data
        return self._get_sample_dashboard(dealer_name)
    
    def _query_dealer_dashboard(self, dealer_name: str) -> Optional[Dict[str, Any]]:
        """
        Query PostgreSQL for complete dealer dashboard
        
        Uses customer_name to find all records for this dealer
        """
        session = self._get_session()
        if not session:
            return None
        
        try:
            # Parameterized SQL query - SAFE
            sql = """
                SELECT 
                    customer_name,
                    dealer_code,
                    customer_code,
                    sales_office,
                    sales_manager,
                    division,
                    warehouse,
                    warehouse_code,
                    ship_to_city,
                    delivery_location,
                    COALESCE(SUM(dn_amount), 0) as revenue,
                    COALESCE(SUM(dn_qty), 0) as units,
                    COUNT(DISTINCT dn_no) as total_dn,
                    COUNT(DISTINCT CASE WHEN delivery_status = 'Delivered' THEN dn_no END) as delivered_dn,
                    COUNT(DISTINCT CASE WHEN pending_flag = true THEN dn_no END) as pending_dn,
                    COUNT(DISTINCT CASE WHEN pgi_status != 'Completed' THEN dn_no END) as pending_pgi,
                    COUNT(DISTINCT CASE WHEN pod_status != 'Completed' THEN dn_no END) as pending_pod,
                    COUNT(DISTINCT customer_model) as product_count,
                    COUNT(DISTINCT material_no) as material_count,
                    COUNT(DISTINCT warehouse) as warehouse_count,
                    COUNT(DISTINCT ship_to_city) as city_count,
                    AVG(CASE WHEN good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date))/86400 END) as avg_delivery_days,
                    AVG(CASE WHEN good_issue_date IS NOT NULL AND pod_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date))/86400 END) as avg_pod_days,
                    MAX(dn_create_date) as latest_dn,
                    MAX(good_issue_date) as latest_pgi,
                    MAX(pod_date) as latest_pod
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER(:dealer_name)
                GROUP BY 
                    customer_name,
                    dealer_code,
                    customer_code,
                    sales_office,
                    sales_manager,
                    division,
                    warehouse,
                    warehouse_code,
                    ship_to_city,
                    delivery_location
            """
            
            result = session.execute(text(sql), {"dealer_name": dealer_name})
            row = result.fetchone()
            
            if not row:
                session.close()
                return None
            
            # Get top product
            product_sql = """
                SELECT customer_model, COUNT(DISTINCT dn_no) as dn_count
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER(:dealer_name)
                AND customer_model IS NOT NULL
                GROUP BY customer_model
                ORDER BY dn_count DESC
                LIMIT 1
            """
            product_result = session.execute(text(product_sql), {"dealer_name": dealer_name})
            product_row = product_result.fetchone()
            
            # Get warehouses
            wh_sql = """
                SELECT DISTINCT warehouse
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER(:dealer_name)
                AND warehouse IS NOT NULL
                ORDER BY warehouse
            """
            wh_result = session.execute(text(wh_sql), {"dealer_name": dealer_name})
            warehouses = [_text(r[0]) for r in wh_result.fetchall()]
            
            # Get cities
            city_sql = """
                SELECT DISTINCT ship_to_city
                FROM delivery_reports
                WHERE LOWER(customer_name) = LOWER(:dealer_name)
                AND ship_to_city IS NOT NULL
                ORDER BY ship_to_city
            """
            city_result = session.execute(text(city_sql), {"dealer_name": dealer_name})
            cities = [_text(r[0]) for r in city_result.fetchall()]
            
            session.close()
            
            # Build profile
            total_dn = int(row.total_dn or 0)
            delivered_dn = int(row.delivered_dn or 0)
            pending_dn = int(row.pending_dn or 0)
            revenue = float(row.revenue or 0)
            units = int(row.units or 0)
            
            delivery_pct = _percent(delivered_dn, total_dn)
            pgi_pct = _percent(
                total_dn - int(row.pending_pgi or 0),
                total_dn
            )
            pod_pct = _percent(
                total_dn - int(row.pending_pod or 0),
                total_dn
            )
            
            # Calculate business score
            business_score = (
                delivery_pct * 0.30 +
                pod_pct * 0.20 +
                (100 - _percent(pending_dn, total_dn)) * 0.20 +
                min(100, revenue / 1000000) * 0.15 +
                min(100, int(row.warehouse_count or 0) * 10) * 0.15
            )
            business_score = round(min(100, max(0, business_score)), 1)
            
            # Generate insights
            insights = []
            if revenue > 10000000:
                insights.append(f"💰 High revenue performer: {format_currency(revenue)}")
            elif revenue > 1000000:
                insights.append(f"💰 Revenue: {format_currency(revenue)}")
            
            if delivery_pct >= 95:
                insights.append("✅ Excellent delivery performance (95%+)")
            elif delivery_pct >= 80:
                insights.append(f"✅ Good delivery performance ({delivery_pct:.1f}%)")
            else:
                insights.append("⚠️ Delivery performance needs improvement")
            
            if pending_dn > 0:
                insights.append(f"⏳ {format_number(pending_dn)} pending delivery notes")
            
            if not insights:
                insights.append("📊 Performance is stable. Continue monitoring.")
            
            # Generate recommendations
            recommendations = []
            if delivery_pct < 85:
                recommendations.append("📦 Improve delivery speed and reliability")
            if pending_dn > 20:
                recommendations.append(f"⏳ Escalate {format_number(pending_dn)} pending DNs")
            if int(row.product_count or 0) < 5:
                recommendations.append("🛒 Expand product portfolio")
            if int(row.warehouse_count or 0) == 1:
                recommendations.append("🏭 Consider warehouse diversification")
            if int(row.city_count or 0) < 3:
                recommendations.append("🌍 Expand to new cities")
            if business_score < 70:
                recommendations.append("📊 Develop action plan to improve business score")
            
            if not recommendations:
                recommendations.append("✅ Maintain current performance levels")
            
            # Build profile
            profile = {
                'name': _text(row.customer_name),
                'dealer_code': _text(row.dealer_code),
                'customer_code': _text(row.customer_code),
                'sales_office': _text(row.sales_office),
                'sales_manager': _text(row.sales_manager),
                'division': _text(row.division),
                'warehouse': _text(row.warehouse),
                'warehouse_code': _text(row.warehouse_code),
                'city': _text(row.ship_to_city),
                'delivery_location': _text(row.delivery_location),
                'revenue': revenue,
                'units': units,
                'total_dn': total_dn,
                'delivered_dn': delivered_dn,
                'pending_dn': pending_dn,
                'pending_pgi': int(row.pending_pgi or 0),
                'pending_pod': int(row.pending_pod or 0),
                'delivery_pct': delivery_pct,
                'pgi_pct': pgi_pct,
                'pod_pct': pod_pct,
                'avg_delivery_days': float(row.avg_delivery_days or 0),
                'avg_pod_days': float(row.avg_pod_days or 0),
                'top_product': _text(product_row[0]) if product_row else "N/A",
                'top_model': _text(product_row[0]) if product_row else "N/A",
                'product_count': int(row.product_count or 0),
                'material_count': int(row.material_count or 0),
                'warehouses_used': warehouses if warehouses else ["N/A"],
                'cities_served': cities if cities else ["N/A"],
                'latest_dn': _date_text(row.latest_dn),
                'latest_pgi': _date_text(row.latest_pgi),
                'latest_pod': _date_text(row.latest_pod),
                'business_score': business_score,
                'insights': insights,
                'recommendations': recommendations
            }
            
            # Build dashboard
            dashboard = self._build_dashboard(profile)
            
            return {
                'success': True,
                'profile': profile,
                'dashboard': dashboard,
                'message': f"✅ Dealer found: {profile['name']}"
            }
            
        except Exception as e:
            logger.error(f"❌ Dashboard query error: {e}")
            if session:
                session.close()
            return None
    
    def _get_sample_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Get sample dashboard for fallback"""
        for key, data in SAMPLE_DEALERS.items():
            if data['name'].lower() == dealer_name.lower():
                profile = {
                    'name': data['name'],
                    'dealer_code': data.get('dealer_code', ''),
                    'customer_code': data.get('customer_code', ''),
                    'sales_office': data.get('sales_office', ''),
                    'sales_manager': data.get('sales_manager', ''),
                    'division': data.get('division', ''),
                    'warehouse': data.get('warehouse', ''),
                    'warehouse_code': data.get('warehouse_code', ''),
                    'city': data.get('city', ''),
                    'delivery_location': data.get('delivery_location', ''),
                    'revenue': data.get('revenue', 0),
                    'units': data.get('units', 0),
                    'total_dn': data.get('total_dn', 0),
                    'delivered_dn': data.get('delivered_dn', 0),
                    'pending_dn': data.get('pending_dn', 0),
                    'pending_pgi': data.get('pending_pgi', 0),
                    'pending_pod': data.get('pending_pod', 0),
                    'delivery_pct': data.get('delivery_pct', 0),
                    'pgi_pct': data.get('pgi_pct', 0),
                    'pod_pct': data.get('pod_pct', 0),
                    'avg_delivery_days': data.get('avg_delivery_days', 0),
                    'avg_pod_days': data.get('avg_pod_days', 0),
                    'top_product': data.get('top_product', 'N/A'),
                    'top_model': data.get('top_model', 'N/A'),
                    'product_count': data.get('product_count', 0),
                    'material_count': data.get('material_count', 0),
                    'warehouses_used': data.get('warehouses_used', []),
                    'cities_served': data.get('cities_served', []),
                    'latest_dn': data.get('latest_dn', 'N/A'),
                    'latest_pgi': data.get('latest_pgi', 'N/A'),
                    'latest_pod': data.get('latest_pod', 'N/A'),
                    'business_score': data.get('business_score', 0),
                    'insights': data.get('insights', []),
                    'recommendations': data.get('recommendations', [])
                }
                
                dashboard = self._build_dashboard(profile)
                
                return {
                    'success': True,
                    'profile': profile,
                    'dashboard': dashboard,
                    'message': f"✅ Dealer found: {profile['name']} (Sample Data)"
                }
        
        return {
            'success': False,
            'message': f"Dealer '{dealer_name}' not found in sample data."
        }
    
    # ============================================================
    # DASHBOARD BUILDER
    # ============================================================
    
    def _build_dashboard(self, profile: Dict) -> str:
        """Build professional dealer dashboard"""
        lines = []
        
        # Header
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏢 DEALER DASHBOARD")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # Identity
        lines.append("📌 DEALER INFORMATION")
        lines.append(f"Name: {profile['name']}")
        if profile.get('dealer_code') and profile['dealer_code'] != "N/A":
            lines.append(f"Dealer Code: {profile['dealer_code']}")
        if profile.get('customer_code') and profile['customer_code'] != "N/A":
            lines.append(f"Customer Code: {profile['customer_code']}")
        if profile.get('sales_office') and profile['sales_office'] != "N/A":
            lines.append(f"Sales Office: {profile['sales_office']}")
        if profile.get('sales_manager') and profile['sales_manager'] != "N/A":
            lines.append(f"Sales Manager: {profile['sales_manager']}")
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
        if profile.get('delivery_location') and profile['delivery_location'] != "N/A":
            lines.append(f"Delivery Location: {profile['delivery_location']}")
        lines.append("")
        
        # Financials
        lines.append("💰 FINANCIALS")
        lines.append(f"Revenue: {format_currency(profile['revenue'])}")
        lines.append(f"Total Units: {format_number(profile['units'])}")
        lines.append("")
        
        # Operations
        lines.append("📦 OPERATIONS")
        lines.append(f"Total DN: {format_number(profile['total_dn'])}")
        lines.append(f"Delivered DN: {format_number(profile['delivered_dn'])}")
        lines.append(f"Pending DN: {format_number(profile['pending_dn'])}")
        lines.append(f"Pending PGI: {format_number(profile['pending_pgi'])}")
        lines.append(f"Pending POD: {format_number(profile['pending_pod'])}")
        lines.append("")
        
        # Delivery
        lines.append("🚚 DELIVERY PERFORMANCE")
        lines.append(f"Delivery Success: {profile['delivery_pct']:.1f}%")
        lines.append(f"PGI Success: {profile['pgi_pct']:.1f}%")
        lines.append(f"POD Success: {profile['pod_pct']:.1f}%")
        lines.append(f"Avg Delivery Days: {profile['avg_delivery_days']:.1f}")
        lines.append(f"Avg POD Days: {profile['avg_pod_days']:.1f}")
        lines.append("")
        
        # Products
        lines.append("🏷️ PRODUCTS")
        lines.append(f"Products: {format_number(profile['product_count'])}")
        lines.append(f"Materials: {format_number(profile['material_count'])}")
        if profile.get('top_product') and profile['top_product'] != "N/A":
            lines.append(f"Top Product: {profile['top_product']}")
        if profile.get('top_model') and profile['top_model'] != "N/A":
            lines.append(f"Top Model: {profile['top_model']}")
        lines.append("")
        
        # Network
        lines.append("🏭 NETWORK")
        lines.append(f"Warehouses: {format_number(len(profile['warehouses_used']))}")
        if profile.get('warehouses_used'):
            display = [w for w in profile['warehouses_used'][:3] if w != "N/A"]
            if display:
                lines.append(f"Used: {', '.join(display)}")
        lines.append(f"Cities Served: {format_number(len(profile['cities_served']))}")
        if profile.get('cities_served'):
            display = [c for c in profile['cities_served'][:3] if c != "N/A"]
            if display:
                lines.append(f"Served: {', '.join(display)}")
        lines.append("")
        
        # Timeline
        lines.append("📅 TIMELINE")
        if profile.get('latest_dn') and profile['latest_dn'] != "N/A":
            lines.append(f"Latest DN: {profile['latest_dn']}")
        if profile.get('latest_pgi') and profile['latest_pgi'] != "N/A":
            lines.append(f"Latest PGI: {profile['latest_pgi']}")
        if profile.get('latest_pod') and profile['latest_pod'] != "N/A":
            lines.append(f"Latest POD: {profile['latest_pod']}")
        lines.append("")
        
        # Business Score
        lines.append("📊 BUSINESS SCORE")
        lines.append(f"Score: {profile['business_score']:.1f}/100")
        lines.append("")
        
        # Insights
        if profile.get('insights'):
            lines.append("💡 INSIGHTS")
            for insight in profile['insights']:
                lines.append(f"• {insight}")
            lines.append("")
        
        # Recommendations
        if profile.get('recommendations'):
            lines.append("🎯 RECOMMENDATIONS")
            for rec in profile['recommendations']:
                lines.append(f"• {rec}")
            lines.append("")
        
        # Footer
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("Search another Dealer")
        lines.append("99️⃣ Main Menu")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check"""
        return {
            "service": "dealer_search_service",
            "version": self._version,
            "status": "healthy",
            "postgresql": "connected" if self._db_available else "fallback",
            "dealers_loaded": len(self._dealer_cache)
        }

# ============================================================
# SINGLETON
# ============================================================

_service: Optional[DealerSearchEngine] = None

def get_dealer_search_engine() -> DealerSearchEngine:
    """Get singleton instance"""
    global _service
    if _service is None:
        _service = DealerSearchEngine()
    return _service

# ============================================================
# MODULE EXPORTS
# ============================================================

__all__ = [
    "DealerSearchEngine",
    "get_dealer_search_engine",
    "EXIT_SIGNAL"
]

# ============================================================
# TEST / STANDALONE MODE
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("DEALER SEARCH ENGINE - TEST MODE".center(60))
    print("=" * 60)
    print()
    
    engine = get_dealer_search_engine()
    
    # Show health
    health = engine.health_check()
    print(f"📊 Health: {health}")
    print()
    
    # Show welcome
    print(engine.get_welcome_message())
    print()
    
    # Interactive test
    while True:
        try:
            query = input("🔍 Enter Dealer Name (or 99 to exit): ").strip()
            
            if query == "99":
                print("\n👋 Goodbye!")
                break
            
            if not query:
                continue
            
            print("\n⏳ Searching...\n")
            result = engine.process_whatsapp_query(query, "test_user")
            
            if result == EXIT_SIGNAL:
                print("Exiting...")
                break
            
            print(result)
            print()
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}\n")
