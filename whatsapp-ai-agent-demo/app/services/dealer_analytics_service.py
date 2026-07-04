#!/usr/bin/env python3
# ============================================================
# FILE: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
# VERSION: 2.0 - HPK LOGISTICS AI DEALER ANALYTICS
# ============================================================

"""
================================================================================
DEALER ANALYTICS SERVICE - HPK LOGISTICS AI
================================================================================

This service handles Dealer Dashboard functionality for HPK Logistics AI.

INTEGRATION WITH GATEWAY:
    - Called by AIProviderService when user selects "Dealer Dashboard" (Option 3)
    - process_whatsapp_query() is the main entry point
    - Returns "99" or "__EXIT__" to unlock session and return to main menu

================================================================================
"""

import logging
import re
from typing import Optional, Dict, List, Any, Union
from datetime import datetime

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

EXIT_SIGNAL = "__EXIT__"

# ============================================================
# DEALER DATABASE
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
# UTILITY FUNCTIONS
# ============================================================

def format_currency(amount: float) -> str:
    """Format currency for display"""
    if amount >= 1_000_000_000:
        return f"PKR {amount/1_000_000_000:,.2f} Billion"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:,.2f} Million"
    else:
        return f"PKR {amount:,.2f}"

def format_number(num: int) -> str:
    """Format number with commas"""
    return f"{num:,}"

def normalize_text(text: str) -> str:
    """Normalize text for matching"""
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip().lower()

# ============================================================
# DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """
    Dealer Analytics Service for HPK Logistics AI
    
    This service handles:
    1. Dealer search and matching
    2. Dealer dashboard generation
    3. WhatsApp message processing
    
    Called by AIProviderService when user selects "Dealer Dashboard"
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
        
        # Session state per user
        self._user_states: Dict[str, Dict] = {}
        
        # Load dealer data
        self._dealer_cache = {}
        self._load_dealers()
        
        logger.info(f"✅ DealerAnalyticsService initialized with {len(self._dealer_cache)} dealers")
    
    def _load_dealers(self):
        """Load dealers from database"""
        for key, data in DEALER_DATABASE.items():
            self._dealer_cache[key] = data
    
    # ============================================================
    # MAIN ENTRY POINT - Called by Gateway
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        MAIN ENTRY POINT - Called by AIProviderService
        
        Args:
            message: User's message text
            sender: User identifier
        
        Returns:
            Response string or EXIT_SIGNAL to unlock session
        """
        try:
            logger.info(f"📨 DealerAnalyticsService.process_whatsapp_query: '{message}' from {sender}")
            
            if not message or not message.strip():
                return self.get_welcome_message()
            
            message_clean = message.strip()
            
            # Check for exit signal
            if message_clean == "99" or message_clean.lower() in ["exit", "quit", "menu"]:
                logger.info(f"🚪 User {sender} requesting exit from Dealer Dashboard")
                return EXIT_SIGNAL
            
            # Get user state
            state = self._user_states.get(sender, {})
            step = state.get('step', 'idle')
            
            # Handle different steps
            if step == 'idle':
                # User is at the welcome screen - search for dealer
                result = self.search_dealer(message_clean)
                
                if result['success']:
                    # Update state
                    self._user_states[sender] = {
                        'step': 'viewing',
                        'dealer': result['profile']['name'],
                        'last_search': message_clean
                    }
                    return result['dashboard']
                else:
                    # Show suggestions or error
                    if result.get('suggestions'):
                        suggestion_text = "\n".join([f"• {s}" for s in result['suggestions'][:3]])
                        return f"❌ {result['message']}\n\n💡 Did you mean:\n{suggestion_text}\n\nPlease try again or type '99' to exit."
                    else:
                        return f"❌ {result['message']}\n\nPlease try a different name or type '99' to exit."
            
            elif step == 'viewing':
                # User is viewing a dealer dashboard
                # Check if they want to search for another dealer
                result = self.search_dealer(message_clean)
                
                if result['success']:
                    # New dealer found
                    self._user_states[sender] = {
                        'step': 'viewing',
                        'dealer': result['profile']['name'],
                        'last_search': message_clean
                    }
                    return result['dashboard']
                else:
                    # Not a dealer name - maybe they want help
                    return "\n".join([
                        "📌 *Currently viewing:*",
                        f"   {state.get('dealer', 'Unknown dealer')}",
                        "",
                        "• Type another dealer name to search",
                        "• Type '99' to exit",
                        "",
                        self.get_help_message()
                    ])
            
            else:
                # Unknown state - reset
                self._user_states[sender] = {'step': 'idle'}
                return self.get_welcome_message()
            
        except Exception as e:
            logger.error(f"❌ DealerAnalyticsService error: {e}", exc_info=True)
            return f"⚠️ An error occurred: {str(e)[:200]}\n\nPlease type '99' to exit."
    
    # ============================================================
    # SEARCH METHODS
    # ============================================================
    
    def search_dealer(self, query: str) -> Dict[str, Any]:
        """
        Search for a dealer using multi-stage matching
        
        Returns:
            Dict with: success, message, profile, dashboard, suggestions
        """
        if not query or not query.strip():
            return {
                'success': False,
                'message': "Please enter a dealer name."
            }
        
        query_clean = query.strip()
        
        # Stage 1: Exact Match
        result = self._exact_match(query_clean)
        if result:
            return self._build_response(result)
        
        # Stage 2: Case Insensitive
        result = self._case_insensitive_match(query_clean)
        if result:
            return self._build_response(result)
        
        # Stage 3: Partial Match
        result = self._partial_match(query_clean)
        if result:
            return self._build_response(result)
        
        # Stage 4: Word Match
        result = self._word_match(query_clean)
        if result:
            return self._build_response(result)
        
        # Stage 5: Dealer Code
        result = self._code_match(query_clean)
        if result:
            return self._build_response(result)
        
        # No match - get suggestions
        suggestions = self._get_suggestions(query_clean)
        
        return {
            'success': False,
            'message': "Dealer not found.",
            'suggestions': suggestions
        }
    
    # ============================================================
    # MATCHING METHODS
    # ============================================================
    
    def _exact_match(self, query: str):
        query_lower = query.lower()
        for key, data in self._dealer_cache.items():
            if key == query_lower:
                return {'dealer': data, 'match_type': 'exact', 'score': 100}
        return None
    
    def _case_insensitive_match(self, query: str):
        query_lower = query.lower()
        for key, data in self._dealer_cache.items():
            if key == query_lower:
                return {'dealer': data, 'match_type': 'case_insensitive', 'score': 99}
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
            return {'dealer': best_match, 'match_type': 'partial', 'score': best_score * 100}
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
            return {'dealer': best_match, 'match_type': 'word', 'score': best_score * 100}
        return None
    
    def _code_match(self, query: str):
        query_clean = query.strip().upper()
        for key, data in self._dealer_cache.items():
            if data['code'] == query_clean or data['customer_code'] == query_clean:
                return {'dealer': data, 'match_type': 'code', 'score': 99}
        return None
    
    def _get_suggestions(self, query: str, limit: int = 3) -> List[str]:
        query_lower = query.lower()
        suggestions = []
        
        for key, data in self._dealer_cache.items():
            if query_lower in key or key in query_lower:
                suggestions.append(data['name'])
                if len(suggestions) >= limit:
                    break
        
        return suggestions
    
    # ============================================================
    # RESPONSE BUILDERS
    # ============================================================
    
    def _build_response(self, match_result: Dict) -> Dict[str, Any]:
        """Build search response with dashboard"""
        dealer = match_result['dealer']
        
        # Build dashboard
        dashboard = self._build_dashboard(dealer)
        
        return {
            'success': True,
            'message': f"✅ Dealer found: {dealer['name']}",
            'profile': dealer,
            'dashboard': dashboard
        }
    
    def _build_dashboard(self, dealer: Dict) -> str:
        """Build professional WhatsApp dashboard"""
        lines = []
        
        # Header
        lines.append("=" * 50)
        lines.append("🏢 DEALER DASHBOARD")
        lines.append("=" * 50)
        lines.append("")
        
        # Identity
        lines.append("📌 IDENTITY")
        lines.append(f"Name: {dealer['name']}")
        if dealer.get('code'):
            lines.append(f"Code: {dealer['code']}")
        if dealer.get('customer_code'):
            lines.append(f"Customer Code: {dealer['customer_code']}")
        if dealer.get('office'):
            lines.append(f"Office: {dealer['office']}")
        if dealer.get('manager'):
            lines.append(f"Manager: {dealer['manager']}")
        if dealer.get('division'):
            lines.append(f"Division: {dealer['division']}")
        lines.append("")
        
        # Location
        lines.append("📍 LOCATION")
        if dealer.get('warehouse'):
            lines.append(f"Warehouse: {dealer['warehouse']}")
        if dealer.get('warehouse_code'):
            lines.append(f"Warehouse Code: {dealer['warehouse_code']}")
        if dealer.get('city'):
            lines.append(f"City: {dealer['city']}")
        lines.append("")
        
        # Financial
        lines.append("💰 FINANCIALS")
        lines.append(f"Revenue: {format_currency(dealer['revenue'])}")
        lines.append(f"Avg Revenue/DN: {format_currency(dealer['avg_revenue_per_dn'])}")
        lines.append(f"Total Units: {format_number(dealer['total_units'])}")
        lines.append(f"Avg Units/DN: {dealer['avg_units_per_dn']:.1f}")
        lines.append("")
        
        # Operations
        lines.append("📦 OPERATIONS")
        lines.append(f"Total DN: {format_number(dealer['total_dn'])}")
        lines.append(f"Pending DN: {format_number(dealer['pending_dn'])}")
        lines.append(f"Delivered DN: {format_number(dealer['delivered_dn'])}")
        lines.append("")
        
        # Delivery
        lines.append("🚚 DELIVERY")
        lines.append(f"Delivery Success: {dealer['delivery_pct']:.1f}%")
        lines.append(f"PGI Success: {dealer['pgi_pct']:.1f}%")
        lines.append(f"POD Success: {dealer['pod_pct']:.1f}%")
        lines.append(f"Avg Delivery Days: {dealer['avg_delivery_days']:.1f}")
        lines.append(f"Avg POD Days: {dealer['avg_pod_days']:.1f}")
        lines.append("")
        
        # Products
        lines.append("🏷️ PRODUCTS")
        lines.append(f"Total Products: {format_number(dealer['product_count'])}")
        if dealer.get('top_product'):
            lines.append(f"Top Product: {dealer['top_product']}")
        lines.append("")
        
        # Warehouses
        lines.append("🏭 WAREHOUSES")
        lines.append(f"Warehouses: {format_number(dealer['warehouse_count'])}")
        if dealer.get('warehouses_used'):
            display = dealer['warehouses_used'][:3]
            lines.append(f"Used: {', '.join(display)}")
            if len(dealer['warehouses_used']) > 3:
                lines.append(f"... and {len(dealer['warehouses_used']) - 3} more")
        lines.append("")
        
        # Cities
        lines.append("🏙️ CITIES")
        lines.append(f"Cities Served: {format_number(dealer['city_count'])}")
        if dealer.get('cities_served'):
            display = dealer['cities_served'][:3]
            lines.append(f"Served: {', '.join(display)}")
            if len(dealer['cities_served']) > 3:
                lines.append(f"... and {len(dealer['cities_served']) - 3} more")
        lines.append("")
        
        # Scores
        lines.append("📊 SCORES")
        lines.append(f"Business Score: {dealer['business_score']:.1f}/100")
        lines.append(f"Risk Score: {dealer['risk_score']:.1f}/100")
        lines.append("")
        
        # Timeline
        lines.append("📅 TIMELINE")
        if dealer.get('first_order'):
            lines.append(f"First Order: {dealer['first_order']}")
        if dealer.get('last_order'):
            lines.append(f"Last Order: {dealer['last_order']}")
        if dealer.get('latest_pod'):
            lines.append(f"Latest POD: {dealer['latest_pod']}")
        if dealer.get('latest_activity'):
            lines.append(f"Latest Activity: {dealer['latest_activity']}")
        lines.append("")
        
        # Insights
        if dealer.get('insights'):
            lines.append("💡 INSIGHTS")
            for insight in dealer['insights']:
                lines.append(f"  • {insight}")
            lines.append("")
        
        # Recommendations
        if dealer.get('recommendations'):
            lines.append("🎯 RECOMMENDATIONS")
            for rec in dealer['recommendations']:
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
    
    def get_help_message(self) -> str:
        """Get help message"""
        return "\n".join([
            "📌 *Dealer Dashboard Help*",
            "",
            "• Type a dealer name to search",
            "• Type '99' to exit",
            "• Type 'help' for this message",
            "",
            "📝 *Available Dealers:*",
        ] + [f"  • {data['name']}" for key, data in list(self._dealer_cache.items())[:6]] + [
            "",
            f"  ... and {len(self._dealer_cache) - 6} more"
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
            "dealers_loaded": len(self._dealer_cache),
            "active_sessions": len(self._user_states)
        }

# ============================================================
# SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None

def get_dealer_service() -> DealerAnalyticsService:
    """
    Get the DealerAnalyticsService singleton instance.
    
    This is the function that AIProviderService looks for.
    """
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
    # Test the service
    print("\n" + "=" * 60)
    print("DEALER ANALYTICS SERVICE - TEST MODE".center(60))
    print("=" * 60)
    print()
    
    service = get_dealer_service()
    
    # Test welcome
    print("📌 Welcome Message:")
    print(service.get_welcome_message())
    print("\n" + "-" * 60)
    
    # Test search
    test_queries = ["zoom", "arshad", "unknown", "99"]
    
    for query in test_queries:
        print(f"\n🔍 Testing: '{query}'")
        print("-" * 40)
        result = service.process_whatsapp_query(query, "test_user")
        print(result[:500] + "..." if len(result) > 500 else result)
        print()
    
    print("=" * 60)
    print("✅ Test Complete")
