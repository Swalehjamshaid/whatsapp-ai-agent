#!/usr/bin/env python3
# ============================================================
# FILE: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
# VERSION: 4.0 - ENTERPRISE DEALER INTELLIGENCE GATEWAY
# ============================================================

"""
================================================================================
DEALER INTELLIGENCE GATEWAY - ENTERPRISE EDITION
================================================================================

This service serves as the orchestration layer for dealer intelligence,
connecting WhatsApp to PostgreSQL through a clean repository pattern.

ARCHITECTURE:
    WhatsApp → DealerAnalyticsService → DealerSessionManager 
    → DealerSearchService → DealerRepository → PostgreSQL
    
RESPONSIBILITIES:
    ✅ Receive WhatsApp messages
    ✅ Manage dealer sessions
    ✅ Orchestrate repository calls
    ✅ Build dealer intelligence dashboard
    ✅ Format WhatsApp responses
    ✅ Cache dashboard results
    ✅ Health monitoring

VERSION: 4.0 - Enterprise Edition
================================================================================
"""

import logging
import time
import json
import hashlib
from typing import Optional, Dict, List, Any, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from functools import lru_cache

logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTS
# ============================================================

EXIT_SIGNAL = "__EXIT__"
VERSION = "4.0"
CACHE_TTL = 300  # 5 minutes cache

# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class DealerSession:
    """Dealer session management"""
    user_id: str
    dealer_name: str = ""
    dealer_code: str = ""
    customer_code: str = ""
    current_dashboard: Dict[str, Any] = field(default_factory=dict)
    last_search: str = ""
    last_activity: datetime = field(default_factory=datetime.now)
    search_count: int = 0
    cache_timestamp: Optional[datetime] = None
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.now()
        self.search_count += 1
    
    def is_cache_valid(self) -> bool:
        """Check if cache is still valid"""
        if not self.cache_timestamp:
            return False
        return (datetime.now() - self.cache_timestamp).seconds < CACHE_TTL
    
    def cache_dashboard(self, dashboard: Dict[str, Any]):
        """Cache the dashboard"""
        self.current_dashboard = dashboard
        self.cache_timestamp = datetime.now()

# ============================================================
# SERVICE IMPORTS
# ============================================================

try:
    from app.services.dealer_search_service import (
        get_dealer_search_engine,
        EXIT_SIGNAL as SEARCH_EXIT_SIGNAL
    )
    SEARCH_AVAILABLE = True
    logger.info("✅ DealerSearchEngine loaded successfully")
except ImportError as e:
    SEARCH_AVAILABLE = False
    logger.error(f"❌ DealerSearchEngine import failed: {e}")

try:
    from app.repositories.dealer_repository import DealerRepository
    REPOSITORY_AVAILABLE = True
    logger.info("✅ DealerRepository loaded successfully")
except ImportError as e:
    REPOSITORY_AVAILABLE = False
    logger.error(f"❌ DealerRepository import failed: {e}")

# ============================================================
# DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """
    Dealer Intelligence Gateway - Enterprise Edition
    
    Orchestrates the complete dealer intelligence workflow:
    1. Session Management
    2. Dealer Search
    3. Data Retrieval (via Repository)
    4. Dashboard Building
    5. Response Formatting
    6. Caching
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
        self._version = VERSION
        self._search_engine = None
        self._repository = None
        self._sessions: Dict[str, DealerSession] = {}
        self._startup_time = datetime.now()
        self._request_count = 0
        self._avg_response_time = 0.0
        
        # Initialize components
        self._initialize_components()
        
        # Display startup screen
        self._show_startup_info()
        
        logger.info("=" * 70)
        logger.info("🚀 DEALER INTELLIGENCE GATEWAY v4.0")
        logger.info("   🎯 Enterprise Edition")
        logger.info("   🔍 Search Engine: ✅")
        logger.info("   🗄️  Repository: ✅")
        logger.info("   💾 Cache: ✅")
        logger.info("   📊 PostgreSQL: ✅")
        logger.info("=" * 70)
    
    # ============================================================
    # INITIALIZATION
    # ============================================================
    
    def _initialize_components(self):
        """Initialize all required components"""
        # Initialize search engine
        if SEARCH_AVAILABLE:
            try:
                self._search_engine = get_dealer_search_engine()
                logger.info("✅ Search engine initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize search engine: {e}")
                self._search_engine = None
        
        # Initialize repository
        if REPOSITORY_AVAILABLE:
            try:
                self._repository = DealerRepository()
                logger.info("✅ Repository initialized")
            except Exception as e:
                logger.error(f"❌ Failed to initialize repository: {e}")
                self._repository = None
    
    def _show_startup_info(self):
        """Display startup information"""
        print("\n" + "=" * 70)
        print("🏢 DEALER INTELLIGENCE GATEWAY v4.0".center(70))
        print("=" * 70)
        print(f"🚀 Started: {self._startup_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🔍 Search Engine: {'✅' if self._search_engine else '❌'}")
        print(f"🗄️  Repository: {'✅' if self._repository else '❌'}")
        print(f"💾 Cache: ✅ (TTL: {CACHE_TTL}s)")
        print("=" * 70)
        
        # Health check
        if self._repository:
            try:
                health = self._repository.health_check()
                print(f"📊 Database: {health.get('status', 'unknown')}")
                print(f"📈 Rows: {health.get('rows', 0):,}")
                print(f"🏢 Dealers: {health.get('dealers', 0):,}")
            except Exception as e:
                print(f"❌ Health check failed: {e}")
        print("=" * 70 + "\n")
    
    # ============================================================
    # MAIN ENTRY POINT
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        MAIN ENTRY POINT - Called by AIProviderService
        
        Args:
            message: User's input from WhatsApp
            sender: User identifier
            
        Returns:
            Formatted response for WhatsApp
        """
        start_time = time.time()
        self._request_count += 1
        
        try:
            logger.info(f"📨 DealerAnalyticsService received: '{message}' from {sender}")
            
            # Validate input
            if not message or not message.strip():
                return self._show_welcome(sender)
            
            message_clean = message.strip()
            
            # Check for exit
            if self._is_exit_command(message_clean):
                logger.info(f"🚪 Exit requested by {sender}")
                return EXIT_SIGNAL
            
            # Check for help/welcome
            if self._is_help_command(message_clean):
                return self._show_welcome(sender)
            
            # Check for examples
            if self._is_examples_command(message_clean):
                return self._show_examples()
            
            # Get or create session
            session = self._get_or_create_session(sender)
            
            # Search for dealer
            dealer_result = self._search_dealer(message_clean, sender)
            
            if not dealer_result.get('success', False):
                return self._format_not_found(message_clean, dealer_result)
            
            # Load dashboard
            dashboard = self._load_dashboard(dealer_result, session)
            
            if not dashboard:
                return "\n".join([
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "❌ UNABLE TO LOAD DASHBOARD",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                    "We couldn't retrieve the dealer dashboard.",
                    "Please try again later.",
                    "",
                    "Type '99' to return to Main Menu",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                ])
            
            # Update session
            self._update_session(session, dealer_result, dashboard)
            
            # Format response
            response = self._format_dashboard(dashboard)
            
            # Log performance
            elapsed = time.time() - start_time
            self._update_performance_metrics(elapsed)
            
            logger.info(f"✅ Dashboard returned in {elapsed*1000:.0f}ms")
            
            return response
            
        except Exception as e:
            logger.error(f"❌ process_whatsapp_query error: {e}")
            logger.error(traceback.format_exc())
            return "\n".join([
                "⚠️ An error occurred while processing your request.",
                "",
                f"Error: {str(e)[:100]}",
                "",
                "Please try again or type '99' to exit."
            ])
    
    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================
    
    def _get_or_create_session(self, user_id: str) -> DealerSession:
        """Get existing session or create new one"""
        if user_id not in self._sessions:
            self._sessions[user_id] = DealerSession(user_id=user_id)
            logger.info(f"🆕 New session created for {user_id}")
        return self._sessions[user_id]
    
    def _update_session(self, session: DealerSession, dealer_result: Dict, dashboard: Dict):
        """Update session with latest data"""
        session.dealer_name = dealer_result.get('customer_name', '')
        session.dealer_code = dealer_result.get('dealer_code', '')
        session.customer_code = dealer_result.get('customer_code', '')
        session.last_search = dealer_result.get('customer_name', '')
        session.cache_dashboard(dashboard)
        session.update_activity()
        logger.info(f"💾 Session updated for {session.user_id}")
    
    def _is_exit_command(self, message: str) -> bool:
        """Check if message is exit command"""
        exit_commands = ["99", "exit", "quit", "back", "main menu", "menu"]
        return message.lower() in exit_commands
    
    def _is_help_command(self, message: str) -> bool:
        """Check if message is help command"""
        help_commands = ["help", "?", "start", "hello", "hi"]
        return message.lower() in help_commands
    
    def _is_examples_command(self, message: str) -> bool:
        """Check if message is examples command"""
        examples_commands = ["examples", "example", "sample"]
        return message.lower() in examples_commands
    
    # ============================================================
    # SEARCH
    # ============================================================
    
    def _search_dealer(self, query: str, sender: str) -> Dict[str, Any]:
        """Search for dealer using search engine"""
        if not self._search_engine:
            return {
                'success': False,
                'message': 'Search engine unavailable'
            }
        
        try:
            # Use search engine
            result = self._search_engine.process_whatsapp_query(query, sender)
            
            # If result is a string, it might be a formatted response
            if isinstance(result, str):
                if result == SEARCH_EXIT_SIGNAL or result == EXIT_SIGNAL:
                    return {'success': False, 'message': 'Exit requested'}
                
                # Check if it looks like a dealer dashboard
                if "DEALER" in result or "DASHBOARD" in result:
                    # Parse the result to extract dealer info
                    return self._parse_search_result(result)
                
                return {'success': False, 'message': result}
            
            # If result is a dict, use it directly
            if isinstance(result, dict):
                return result
            
            return {'success': False, 'message': 'Invalid search result'}
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            return {'success': False, 'message': str(e)}
    
    def _parse_search_result(self, result: str) -> Dict[str, Any]:
        """Parse search result to extract dealer information"""
        # This is a simplified parser - in production, use proper parsing
        lines = result.split('\n')
        dealer_info = {}
        
        for line in lines:
            if 'customer_name' in line.lower() or 'dealer' in line.lower():
                if ':' in line:
                    key, value = line.split(':', 1)
                    dealer_info[key.strip()] = value.strip()
        
        if dealer_info:
            dealer_info['success'] = True
            return dealer_info
        
        return {'success': False, 'message': 'Could not parse dealer information'}
    
    # ============================================================
    # DASHBOARD LOADING
    # ============================================================
    
    def _load_dashboard(self, dealer_result: Dict, session: DealerSession) -> Optional[Dict[str, Any]]:
        """Load dealer dashboard from cache or repository"""
        
        # Check cache first
        if session.is_cache_valid() and session.current_dashboard:
            logger.info(f"💾 Cache hit for {session.dealer_name}")
            return session.current_dashboard
        
        # Load from repository
        if not self._repository:
            logger.error("❌ Repository not available")
            return None
        
        try:
            dealer_code = dealer_result.get('dealer_code', '')
            customer_code = dealer_result.get('customer_code', '')
            dealer_name = dealer_result.get('customer_name', '')
            
            logger.info(f"📊 Loading dashboard for {dealer_name}")
            
            # Build dashboard from repository
            dashboard = {
                'dealer_info': self._repository.get_dealer_identity(dealer_code, customer_code),
                'delivery_summary': self._repository.get_delivery_summary(dealer_code),
                'business_summary': self._repository.get_business_summary(dealer_code),
                'product_summary': self._repository.get_product_summary(dealer_code),
                'operation_summary': self._repository.get_operation_summary(dealer_code),
                'performance': self._repository.get_performance_summary(dealer_code),
                'insights': self._repository.get_business_insights(dealer_code),
                'latest_activity': self._repository.get_latest_activity(dealer_code)
            }
            
            logger.info(f"✅ Dashboard loaded for {dealer_name}")
            return dashboard
            
        except Exception as e:
            logger.error(f"❌ Failed to load dashboard: {e}")
            return None
    
    def _load_cached_dashboard(self, session: DealerSession) -> Optional[Dict[str, Any]]:
        """Load cached dashboard if valid"""
        if session.is_cache_valid():
            return session.current_dashboard
        return None
    
    def _refresh_dashboard(self, dealer_code: str) -> Optional[Dict[str, Any]]:
        """Force refresh dashboard from repository"""
        if not self._repository:
            return None
        
        try:
            return self._load_dashboard({'dealer_code': dealer_code}, 
                                       DealerSession(user_id="refresh"))
        except Exception as e:
            logger.error(f"❌ Failed to refresh dashboard: {e}")
            return None
    
    # ============================================================
    # DASHBOARD FORMATTING
    # ============================================================
    
    def _format_dashboard(self, dashboard: Dict[str, Any]) -> str:
        """Format dashboard for WhatsApp response"""
        lines = []
        
        # ============================================================
        # HEADER
        # ============================================================
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🏢 DEALER INTELLIGENCE")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        # ============================================================
        # DEALER INFORMATION
        # ============================================================
        dealer_info = dashboard.get('dealer_info', {})
        lines.append("👤 Dealer")
        lines.append(dealer_info.get('customer_name', 'N/A'))
        lines.append("")
        lines.append("🆔 Dealer Code")
        lines.append(dealer_info.get('dealer_code', 'N/A'))
        lines.append("")
        lines.append("🆔 Customer Code")
        lines.append(dealer_info.get('customer_code', 'N/A'))
        lines.append("")
        
        # ============================================================
        # LOCATION
        # ============================================================
        lines.append("📍 LOCATION")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("City")
        lines.append(dealer_info.get('city', 'N/A'))
        lines.append("")
        lines.append("Warehouse")
        lines.append(dealer_info.get('warehouse', 'N/A'))
        lines.append("")
        lines.append("Warehouse Code")
        lines.append(dealer_info.get('warehouse_code', 'N/A'))
        lines.append("")
        lines.append("Delivery Location")
        lines.append(dealer_info.get('delivery_location', 'N/A'))
        lines.append("")
        lines.append("👔 Sales Office")
        lines.append(dealer_info.get('sales_office', 'N/A'))
        lines.append("")
        lines.append("👨‍💼 Sales Channel")
        lines.append(dealer_info.get('sales_channel', 'N/A'))
        lines.append("")
        
        # ============================================================
        # DELIVERY SUMMARY
        # ============================================================
        delivery = dashboard.get('delivery_summary', {})
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📦 DELIVERY SUMMARY")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"🚚 Total DN           : {delivery.get('total_dn', 0)}")
        lines.append(f"✅ Delivered DN       : {delivery.get('delivered_dn', 0)}")
        lines.append(f"⏳ Pending DN         : {delivery.get('pending_dn', 0)}")
        lines.append("")
        lines.append(f"📤 PGI Completed      : {delivery.get('pgi_completed', 0)}")
        lines.append(f"📥 POD Completed      : {delivery.get('pod_completed', 0)}")
        lines.append("")
        lines.append(f"📊 Delivery Rate      : {delivery.get('delivery_rate', 0):.2f}%")
        lines.append(f"📊 PGI Rate           : {delivery.get('pgi_rate', 0):.2f}%")
        lines.append(f"📊 POD Rate           : {delivery.get('pod_rate', 0):.2f}%")
        lines.append("")
        lines.append(f"🚚 Avg Delivery Days  : {delivery.get('avg_delivery_days', 0):.1f} Days")
        lines.append(f"📥 Avg POD Days       : {delivery.get('avg_pod_days', 0):.1f} Days")
        lines.append("")
        
        # ============================================================
        # BUSINESS SUMMARY
        # ============================================================
        business = dashboard.get('business_summary', {})
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💰 BUSINESS SUMMARY")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"💵 Total Revenue")
        lines.append(self._format_currency(business.get('total_revenue', 0)))
        lines.append("")
        lines.append(f"📦 Total Units Sold")
        lines.append(f"{business.get('total_units', 0):,}")
        lines.append("")
        lines.append(f"📄 Total Delivery Notes")
        lines.append(f"{business.get('total_dn', 0)}")
        lines.append("")
        lines.append(f"💰 Average Revenue / DN")
        lines.append(self._format_currency(business.get('avg_revenue_per_dn', 0)))
        lines.append("")
        lines.append(f"📦 Average Units / DN")
        lines.append(f"{business.get('avg_units_per_dn', 0):.2f}")
        lines.append("")
        
        # ============================================================
        # PRODUCT SUMMARY
        # ============================================================
        product = dashboard.get('product_summary', {})
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📦 PRODUCT SUMMARY")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("Products Sold")
        lines.append(str(product.get('products_sold', 0)))
        lines.append("")
        lines.append("Models")
        lines.append(str(product.get('models_count', 0)))
        lines.append("")
        lines.append("Materials")
        lines.append(str(product.get('materials_count', 0)))
        lines.append("")
        lines.append("Top Product")
        lines.append(product.get('top_product', 'N/A'))
        lines.append("")
        lines.append("Top Model")
        lines.append(product.get('top_model', 'N/A'))
        lines.append("")
        lines.append("Top Material")
        lines.append(product.get('top_material', 'N/A'))
        lines.append("")
        lines.append("Primary Division")
        lines.append(product.get('primary_division', 'N/A'))
        lines.append("")
        
        # ============================================================
        # OPERATION SUMMARY
        # ============================================================
        operation = dashboard.get('operation_summary', {})
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📍 OPERATION SUMMARY")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("Cities Served")
        lines.append(str(operation.get('cities_served', 0)))
        lines.append("")
        lines.append("Warehouses Used")
        lines.append(str(operation.get('warehouses_used', 0)))
        lines.append("")
        lines.append("Primary Warehouse")
        lines.append(operation.get('primary_warehouse', 'N/A'))
        lines.append("")
        lines.append("Latest DN")
        lines.append(operation.get('latest_dn', 'N/A'))
        lines.append("")
        lines.append("Latest PGI")
        lines.append(operation.get('latest_pgi', 'N/A'))
        lines.append("")
        lines.append("Latest POD")
        lines.append(operation.get('latest_pod', 'N/A'))
        lines.append("")
        
        # ============================================================
        # PERFORMANCE
        # ============================================================
        performance = dashboard.get('performance', {})
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📈 PERFORMANCE")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        score = performance.get('business_score', 0)
        score_emoji = "🟢" if score >= 90 else "🟡" if score >= 70 else "🔴"
        lines.append("Business Score")
        lines.append(f"{score} / 100 {score_emoji}")
        lines.append("")
        lines.append("Revenue Rank")
        lines.append(f"#{performance.get('revenue_rank', 0)}")
        lines.append("")
        lines.append("Delivery Rank")
        lines.append(f"#{performance.get('delivery_rank', 0)}")
        lines.append("")
        lines.append("Overall Rank")
        lines.append(f"#{performance.get('overall_rank', 0)}")
        lines.append("")
        
        # ============================================================
        # BUSINESS INSIGHTS
        # ============================================================
        insights = dashboard.get('insights', [])
        if insights:
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 BUSINESS INSIGHTS")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("")
            for insight in insights:
                lines.append(insight)
                lines.append("")
        
        # ============================================================
        # FOOTER
        # ============================================================
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💬 Type '99' to return to Main Menu")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)
    
    # ============================================================
    # UTILITY METHODS
    # ============================================================
    
    def _format_currency(self, amount: float) -> str:
        """Format currency for display"""
        if amount >= 1000000:
            return f"PKR {amount/1000000:.1f}M"
        elif amount >= 1000:
            return f"PKR {amount/1000:.1f}K"
        else:
            return f"PKR {amount:,.0f}"
    
    def _format_not_found(self, query: str, result: Dict) -> str:
        """Format not found response"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🔍 DEALER NOT FOUND",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"We couldn't find '{query}' in our records.",
            "",
            "💡 Suggestions:",
            "• Check the spelling",
            "• Try searching by Dealer Code",
            "• Try searching by Customer Code",
            "• Use partial name search",
            "",
            "📝 Examples:",
            "• Arshad Electronics-Khi",
            "• Zoom Appliances",
            "• RUBA Digital",
            "",
            "99️⃣ Return to Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _format_not_found(self, query: str, result: Dict) -> str:
        """Format not found response"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🔍 DEALER NOT FOUND",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"We couldn't find '{query}' in our records.",
            "",
            "💡 Suggestions:",
            "• Check the spelling",
            "• Try searching by Dealer Code",
            "• Try searching by Customer Code",
            "• Use partial name search",
            "",
            "📝 Examples:",
            "• Arshad Electronics-Khi",
            "• Zoom Appliances",
            "• RUBA Digital",
            "",
            "99️⃣ Return to Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    # ============================================================
    # WELCOME AND EXAMPLES
    # ============================================================
    
    def _show_welcome(self, sender: str = None) -> str:
        """Show welcome message"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🏢 DEALER SEARCH",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Please write the Dealer Name.",
            "",
            "Examples:",
            "• Arshad Electronics-Khi",
            "• Zoom Appliances",
            "• RUBA Digital",
            "• Metro Electronics",
            "• Friends Electronics",
            "",
            "Supported Search:",
            "✓ Dealer Name",
            "✓ Dealer Code",
            "✓ Customer Code",
            "✓ Partial Search",
            "✓ Alias",
            "✓ Smart Match (70%)",
            "",
            "99️⃣ Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _show_examples(self) -> str:
        """Show example dealer names"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📝 DEALER EXAMPLES",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Try searching for:",
            "",
            "1. Arshad Electronics-Khi",
            "2. Zoom Appliances",
            "3. RUBA Digital",
            "4. Metro Electronics",
            "5. Friends Electronics",
            "6. Al Madina Electronics",
            "7. Galaxy Electronics",
            "8. Star Traders",
            "",
            "💡 You can also search by:",
            "• Dealer Code (e.g., DLR-045)",
            "• Customer Code (e.g., CUST-789)",
            "",
            "99️⃣ Return to Main Menu",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    # ============================================================
    # PERFORMANCE METRICS
    # ============================================================
    
    def _update_performance_metrics(self, elapsed: float):
        """Update performance metrics"""
        self._avg_response_time = ((self._avg_response_time * (self._request_count - 1)) + elapsed) / self._request_count
    
    def performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics"""
        return {
            'total_requests': self._request_count,
            'avg_response_time': self._avg_response_time * 1000,  # ms
            'active_sessions': len(self._sessions),
            'cache_hits': 0,  # Track this
            'uptime': (datetime.now() - self._startup_time).seconds
        }
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for the service"""
        health = {
            "service": "dealer_analytics_service",
            "version": self._version,
            "status": "healthy",
            "search_engine": "available" if self._search_engine else "unavailable",
            "repository": "available" if self._repository else "unavailable",
            "active_sessions": len(self._sessions),
            "uptime": (datetime.now() - self._startup_time).seconds
        }
        
        # Check repository health
        if self._repository:
            try:
                repo_health = self._repository.health_check()
                health.update(repo_health)
            except Exception as e:
                health["repository_health"] = f"Error: {e}"
                health["status"] = "degraded"
        
        return health
    
    # ============================================================
    # CACHE MANAGEMENT
    # ============================================================
    
    def clear_cache(self, user_id: str = None):
        """Clear cache for specific user or all users"""
        if user_id:
            if user_id in self._sessions:
                self._sessions[user_id].current_dashboard = {}
                self._sessions[user_id].cache_timestamp = None
                logger.info(f"💾 Cache cleared for {user_id}")
        else:
            for session in self._sessions.values():
                session.current_dashboard = {}
                session.cache_timestamp = None
            logger.info("💾 All caches cleared")

# ============================================================
# SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None

def get_dealer_service() -> DealerAnalyticsService:
    """
    Get singleton instance of DealerAnalyticsService.
    
    This is the function referenced in AIProviderService:
    "function": "get_dealer_service"
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
    "EXIT_SIGNAL",
    "DealerSession"
]

# ============================================================
# TEST / STANDALONE MODE
# ============================================================

if __name__ == "__main__":
    import traceback
    
    print("\n" + "=" * 70)
    print("DEALER INTELLIGENCE GATEWAY v4.0 - TEST MODE".center(70))
    print("=" * 70)
    print()
    
    service = get_dealer_service()
    
    # Show health
    health = service.health_check()
    print("📊 Health Check:")
    for key, value in health.items():
        print(f"   {key}: {value}")
    print()
    
    # Show welcome
    print(service._show_welcome())
    print()
    
    # Interactive test
    print("🔍 INTERACTIVE TEST MODE")
    print("Enter dealer name to search (or 99 to exit)")
    print()
    
    while True:
        try:
            query = input("🔍 Enter Dealer Name: ").strip()
            
            if query == "99":
                print("\n👋 Goodbye!")
                break
            
            if not query:
                continue
            
            print("\n⏳ Processing...\n")
            result = service.process_whatsapp_query(query, "test_user")
            
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
            traceback.print_exc()
