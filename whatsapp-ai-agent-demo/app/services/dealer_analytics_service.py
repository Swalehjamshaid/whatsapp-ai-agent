#!/usr/bin/env python3
# ============================================================
# FILE: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
# VERSION: 2.0 - INTEGRATED WITH DEALER SEARCH
# ============================================================

"""
================================================================================
DEALER ANALYTICS SERVICE - WHATSAPP INTEGRATION
================================================================================

This service integrates with the AIProviderService gateway and provides
dealer analytics and search functionality via WhatsApp.

INTEGRATION:
    - Called by AIProviderService when user selects "Dealer Dashboard" (Option 3)
    - Uses DealerSearchEngine for dealer lookup
    - Returns formatted dashboards for WhatsApp display

FLOW:
    1. User selects Dealer Dashboard (Option 3)
    2. Service shows welcome message with available dealers
    3. User types dealer name or code
    4. Service searches and returns dealer dashboard
    5. User can search multiple dealers
    6. Type '99' to exit back to main menu

================================================================================
"""

import logging
import re
import traceback
from typing import Optional, Dict, List, Any, Union
from datetime import datetime, date

logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTS
# ============================================================

EXIT_SIGNAL = "__EXIT__"
WELCOME_MESSAGE = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏢 DEALER DASHBOARD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Search for any dealer to view their:
• Revenue & Sales Performance
• Delivery Success Rate
• Product Portfolio
• Regional Coverage
• Business Score & Insights

📝 Enter a dealer name to get started.

Examples:
• Arshad Electronics-Khi
• Zoom Appliances
• Metro Electronics
• Al Madina Electronics

💡 You can also search by:
• Dealer Code
• Customer Code
• Partial Name

99️⃣ Return to Main Menu
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ============================================================
# DEALER SEARCH ENGINE IMPORT
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
    logger.warning("⚠️ Running in limited mode - dealer search unavailable")

# ============================================================
# DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """
    Dealer Analytics Service - WhatsApp Integration Layer
    
    This service acts as a wrapper around DealerSearchEngine,
    providing WhatsApp-friendly responses and session management.
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
        self._version = "2.0"
        self._search_engine = None
        
        # Initialize search engine if available
        if SEARCH_AVAILABLE:
            try:
                self._search_engine = get_dealer_search_engine()
                logger.info("✅ DealerAnalyticsService initialized with search engine")
            except Exception as e:
                logger.error(f"❌ Failed to initialize search engine: {e}")
                self._search_engine = None
        else:
            logger.warning("⚠️ DealerAnalyticsService running without search engine")
        
        logger.info("=" * 60)
        logger.info("🚀 DEALER ANALYTICS SERVICE v2.0")
        logger.info(f"   🔍 Search Engine: {'✅' if self._search_engine else '❌'}")
        logger.info("   📱 WhatsApp Integration: ✅")
        logger.info("=" * 60)
    
    # ============================================================
    # MAIN ENTRY POINT - Called by AIProviderService
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        MAIN ENTRY POINT - Called by AIProviderService when in Dealer Dashboard.
        
        Args:
            message: User's input from WhatsApp
            sender: User identifier
            
        Returns:
            Formatted response for WhatsApp
        """
        try:
            logger.info(f"📨 DealerAnalyticsService received: '{message}' from {sender}")
            
            if not message or not message.strip():
                return self.get_welcome_message()
            
            message_clean = message.strip()
            
            # Check for exit
            if message_clean == "99" or message_clean.lower() in ["exit", "quit", "back", "main menu"]:
                logger.info(f"🚪 Exit requested by {sender}")
                return EXIT_SIGNAL
            
            # Check if search engine is available
            if not self._search_engine:
                return "\n".join([
                    "❌ Dealer search is currently unavailable.",
                    "",
                    "Please try again later or contact support.",
                    "",
                    "Type '99' to return to the main menu."
                ])
            
            # Check for help
            if message_clean.lower() in ["help", "menu", "?"]:
                return self.get_welcome_message()
            
            # Search for dealer
            result = self._search_engine.process_whatsapp_query(message_clean, sender)
            
            # Check if search engine returned exit signal
            if result == SEARCH_EXIT_SIGNAL or result == EXIT_SIGNAL:
                logger.info(f"🚪 Search engine requested exit for {sender}")
                return EXIT_SIGNAL
            
            # Return the result (dashboard or suggestions)
            return result
            
        except Exception as e:
            logger.error(f"❌ process_whatsapp_query error: {e}")
            logger.error(traceback.format_exc())
            return "\n".join([
                "⚠️ An error occurred while searching.",
                "",
                f"Error: {str(e)[:100]}",
                "",
                "Please try again or type '99' to exit."
            ])
    
    # ============================================================
    # WELCOME MESSAGE
    # ============================================================
    
    def get_welcome_message(self) -> str:
        """Get welcome message for Dealer Dashboard"""
        if self._search_engine:
            try:
                # Get dealer count from search engine
                health = self._search_engine.health_check()
                dealer_count = health.get('dealers_loaded', 0)
                status = health.get('postgresql', 'unknown')
                
                # Add status indicator
                if status == "connected":
                    status_text = "✅ Live Database"
                else:
                    status_text = "📚 Sample Data (Demo)"
                
                # Add dealer count to welcome
                return "\n".join([
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "🏢 DEALER DASHBOARD",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                    f"📊 {dealer_count} dealers available",
                    f"📡 {status_text}",
                    "",
                    "📝 Enter a dealer name to view their dashboard:",
                    "",
                    "Examples:",
                    "• Arshad Electronics-Khi",
                    "• Zoom Appliances",
                    "• Metro Electronics",
                    "• Al Madina Electronics",
                    "",
                    "💡 Search by:",
                    "• Dealer Name (customer_name)",
                    "• Dealer Code (dealer_code)",
                    "• Customer Code (customer_code)",
                    "• Partial Name",
                    "",
                    "99️⃣ Return to Main Menu",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                ])
            except Exception as e:
                logger.error(f"Error getting welcome message: {e}")
                return WELCOME_MESSAGE
        else:
            return "\n".join([
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "🏢 DEALER DASHBOARD",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "❌ Dealer search service is unavailable.",
                "",
                "Please contact support.",
                "",
                "99️⃣ Return to Main Menu",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ])
    
    # ============================================================
    # DIRECT SEARCH (for non-WhatsApp contexts)
    # ============================================================
    
    def search_dealer(self, query: str) -> Dict[str, Any]:
        """
        Direct search method for programmatic access.
        
        Args:
            query: Dealer name or code to search
            
        Returns:
            Dictionary with search results
        """
        if not self._search_engine:
            return {
                'success': False,
                'message': 'Search engine unavailable'
            }
        
        try:
            # Use the search engine's internal search
            result = self._search_engine.search_dealer(query)
            return result
        except Exception as e:
            logger.error(f"Search error: {e}")
            return {
                'success': False,
                'message': str(e)
            }
    
    # ============================================================
    # LIST ALL DEALERS
    # ============================================================
    
    def list_all_dealers(self) -> List[str]:
        """Get list of all dealer names"""
        if not self._search_engine:
            return []
        
        try:
            return self._search_engine.list_all_dealers()
        except Exception as e:
            logger.error(f"Error listing dealers: {e}")
            return []
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for the service"""
        health = {
            "service": "dealer_analytics_service",
            "version": self._version,
            "status": "healthy",
            "search_engine": "available" if self._search_engine else "unavailable"
        }
        
        if self._search_engine:
            try:
                search_health = self._search_engine.health_check()
                health.update(search_health)
            except Exception as e:
                health["search_engine_health"] = f"Error: {e}"
        
        return health

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
    
    # Show welcome
    print(service.get_welcome_message())
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
            result = service.process_whatsapp_query(query, "test_user")
            
            if result == EXIT_SIGNAL:
                print("Exiting...")
                break
            
            print(result)
            print()
            
            # Show available dealers option
            if query.lower() in ["list", "all", "dealers"]:
                dealers = service.list_all_dealers()
                print("📋 Available Dealers:")
                for i, name in enumerate(dealers[:20], 1):
                    print(f"   {i}. {name}")
                print()
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}\n")
