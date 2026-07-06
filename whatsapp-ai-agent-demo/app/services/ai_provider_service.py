#!/usr/bin/env python3
# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 3.0 - PURE GATEWAY & SESSION MANAGER
# ============================================================

"""
================================================================================
AI PROVIDER SERVICE - PURE GATEWAY & SESSION MANAGER
================================================================================

This file is ONLY the Gateway, Router, Menu Controller, and Session Manager.

It does NOT:
- Execute SQL
- Detect business intent
- Calculate KPI
- Search dealer/warehouse/city/DN
- Answer AI questions
- Create analytics
- Build dashboards

It ONLY:
- Receives WhatsApp messages
- Manages sessions
- Shows Main Menu
- Locks selected modules
- Forwards messages to services
- Unlocks on 99

Architecture:
    WhatsApp → webhook.py → process_whatsapp_query() → 
        Session Manager → Menu Controller → Router → Service

Services:
    1 → national_kpi_service.py
    2 → dn_analysis.py
    3 → dealer_analytics_service.py
    4 → warehouse_service.py
    5 → product_service.py
    6 → city_service.py
    7 → groq_service.py
================================================================================
"""

from __future__ import annotations

import logging
import asyncio
from typing import Dict, Optional, Any
from datetime import datetime
import traceback

logger = logging.getLogger(__name__)

# ============================================================
# SERVICE IMPORTS - PUBLIC ENTRY FUNCTIONS ONLY
# ============================================================

# 1. National KPI Service
try:
    from app.services.national_kpi_service import get_kpi_service
    KPI_AVAILABLE = True
    logger.info("✅ National KPI Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ National KPI Service not available: {e}")
    get_kpi_service = None
    KPI_AVAILABLE = False

# 2. DN Analysis Service
try:
    from app.services.dn_analysis import get_dn_analysis_service
    DN_AVAILABLE = True
    logger.info("✅ DN Analysis Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ DN Analysis Service not available: {e}")
    get_dn_analysis_service = None
    DN_AVAILABLE = False

# 3. Dealer Analytics Service
try:
    from app.services.dealer_analytics_service import get_dealer_service, EXIT_SIGNAL
    DEALER_AVAILABLE = True
    logger.info("✅ Dealer Analytics Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Dealer Analytics Service not available: {e}")
    get_dealer_service = None
    EXIT_SIGNAL = "__EXIT__"
    DEALER_AVAILABLE = False

# 4. Warehouse Service
try:
    from app.services.warehouse_service import get_warehouse_service
    WAREHOUSE_AVAILABLE = True
    logger.info("✅ Warehouse Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Warehouse Service not available: {e}")
    get_warehouse_service = None
    WAREHOUSE_AVAILABLE = False

# 5. Product Service
try:
    from app.services.product_service import get_product_service
    PRODUCT_AVAILABLE = True
    logger.info("✅ Product Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Product Service not available: {e}")
    get_product_service = None
    PRODUCT_AVAILABLE = False

# 6. City Service
try:
    from app.services.city_service import get_city_service
    CITY_AVAILABLE = True
    logger.info("✅ City Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ City Service not available: {e}")
    get_city_service = None
    CITY_AVAILABLE = False

# 7. Groq AI Service
try:
    from app.services.groq_service import get_groq_service
    GROQ_AVAILABLE = True
    logger.info("✅ Groq AI Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Groq AI Service not available: {e}")
    get_groq_service = None
    GROQ_AVAILABLE = False

# ============================================================
# CONSTANTS
# ============================================================

VERSION = "3.0"
SESSION_TIMEOUT_SECONDS = 1800  # 30 minutes

# ============================================================
# SESSION DATA CLASS
# ============================================================

class SessionData:
    """User session data - pure session state only"""
    def __init__(self, phone: str):
        self.phone: str = phone
        self.locked: bool = False
        self.locked_service: Optional[str] = None
        self.created_at: datetime = datetime.now()
        self.last_activity: datetime = datetime.now()
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.now()
    
    def is_expired(self) -> bool:
        """Check if session has expired (30 minutes timeout)"""
        elapsed = (datetime.now() - self.last_activity).total_seconds()
        return elapsed > SESSION_TIMEOUT_SECONDS
    
    def lock(self, service_name: str):
        """Lock session to a specific service"""
        self.locked = True
        self.locked_service = service_name
        self.update_activity()
    
    def unlock(self):
        """Unlock session"""
        self.locked = False
        self.locked_service = None
        self.update_activity()

# ============================================================
# AI PROVIDER SERVICE - PURE ROUTER
# ============================================================

class AIProviderService:
    """
    Pure Gateway, Router, Menu Controller, and Session Manager.
    
    This class does NOT contain any business logic, SQL, AI, or analytics.
    It ONLY routes messages to the appropriate services.
    """
    
    _instance: Optional["AIProviderService"] = None
    _sessions: Dict[str, SessionData] = {}
    _lock: asyncio.Lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        
        self._initialized = True
        self._version = VERSION
        self._startup = datetime.now()
        self._service_handlers = {
            "1": self._handle_kpi,
            "2": self._handle_dn,
            "3": self._handle_dealer,
            "4": self._handle_warehouse,
            "5": self._handle_product,
            "6": self._handle_city,
            "7": self._handle_groq,
        }
        self._service_names = {
            "1": "National KPI",
            "2": "DN Analysis",
            "3": "Dealer Analytics",
            "4": "Warehouse Analytics",
            "5": "Product Analytics",
            "6": "City Analytics",
            "7": "AI Assistant",
        }
        
        self._show_startup()
    
    def _show_startup(self):
        """Display startup information"""
        print("\n" + "=" * 70)
        print("🤖 AI PROVIDER GATEWAY v{} - PURE ROUTER".center(70).format(self._version))
        print("=" * 70)
        print("📋 SERVICES AVAILABLE:")
        print("-" * 70)
        
        services = {
            "1": ("National KPI", KPI_AVAILABLE),
            "2": ("DN Analysis", DN_AVAILABLE),
            "3": ("Dealer Analytics", DEALER_AVAILABLE),
            "4": ("Warehouse Analytics", WAREHOUSE_AVAILABLE),
            "5": ("Product Analytics", PRODUCT_AVAILABLE),
            "6": ("City Analytics", CITY_AVAILABLE),
            "7": ("Groq AI", GROQ_AVAILABLE),
        }
        
        for key, (name, available) in services.items():
            status = "✅" if available else "❌"
            print(f"  {key}. {status} {name}")
        
        print("-" * 70)
        print(f"  🕐 Started at: {self._startup.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70 + "\n")
    
    # ============================================================
    # MAIN ENTRY POINT - DO NOT CHANGE SIGNATURE
    # ============================================================
    
    async def process_whatsapp_query(self, message: str, sender: str) -> str:
        """
        Main entry point for WhatsApp queries.
        
        This is called by webhook.py. DO NOT CHANGE SIGNATURE.
        
        Args:
            message: User's message
            sender: Sender's phone number
            
        Returns:
            Response string
        """
        try:
            logger.info(f"📨 Incoming: '{message}' from {sender}")
            
            if not message or not message.strip():
                return self._get_main_menu()
            
            # Clean message
            msg = message.strip()
            
            # Get or create session
            session = await self._get_or_create_session(sender)
            
            # Check if session is expired
            if session.is_expired():
                logger.info(f"⏰ Session expired for {sender}")
                # Remove expired session
                async with self._lock:
                    if sender in self._sessions:
                        del self._sessions[sender]
                return self._get_main_menu()
            
            # Update activity timestamp
            session.update_activity()
            
            # Check if session is locked
            if session.locked:
                # Session is locked - forward directly to locked service
                logger.info(f"🔒 Session locked to {session.locked_service} for {sender}")
                response = await self._forward_to_locked_service(msg, session)
                return response
            
            # Session is NOT locked - show menu or handle selection
            return await self._handle_unlocked_session(msg, sender, session)
            
        except Exception as e:
            logger.error(f"❌ Error in process_whatsapp_query: {e}")
            logger.error(traceback.format_exc())
            return self._get_error_message()
    
    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================
    
    async def _get_or_create_session(self, phone: str) -> SessionData:
        """Get or create session for phone number"""
        async with self._lock:
            if phone in self._sessions:
                session = self._sessions[phone]
                # Check if session is expired
                if session.is_expired():
                    logger.info(f"⏰ Session expired for {phone}, creating new")
                    del self._sessions[phone]
                    session = SessionData(phone)
                    self._sessions[phone] = session
                return session
            
            # Create new session
            session = SessionData(phone)
            self._sessions[phone] = session
            logger.info(f"🆕 New session created for {phone}")
            return session
    
    async def _lock_session(self, phone: str, service_key: str) -> Optional[SessionData]:
        """Lock session to a specific service"""
        async with self._lock:
            if phone not in self._sessions:
                return None
            
            session = self._sessions[phone]
            service_name = self._service_names.get(service_key, "Unknown")
            session.lock(service_name)
            logger.info(f"🔒 Session locked to {service_name} for {phone}")
            return session
    
    async def _unlock_session(self, phone: str) -> Optional[SessionData]:
        """Unlock session"""
        async with self._lock:
            if phone not in self._sessions:
                return None
            
            session = self._sessions[phone]
            if session.locked:
                logger.info(f"🔓 Session unlocked for {phone} (was {session.locked_service})")
            session.unlock()
            return session
    
    async def _get_session(self, phone: str) -> Optional[SessionData]:
        """Get session without creating"""
        async with self._lock:
            return self._sessions.get(phone)
    
    # ============================================================
    # MENU CONTROLLER
    # ============================================================
    
    def _get_main_menu(self) -> str:
        """Return the exact main menu"""
        return "\n".join([
            "📦 DN INTELLIGENCE CENTER",
            "",
            "1. National KPI Dashboard",
            "",
            "2. DN Analysis",
            "",
            "3. Dealer Analytics",
            "",
            "4. Warehouse Analytics",
            "",
            "5. Product Analytics",
            "",
            "6. City Analytics",
            "",
            "7. AI Assistant",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "Reply with:",
            "",
            "1 - National KPI",
            "2 - DN Analysis",
            "3 - Dealer Analytics",
            "4 - Warehouse Analytics",
            "5 - Product Analytics",
            "6 - City Analytics",
            "7 - AI Assistant",
            "99 - Return to Main Menu"
        ])
    
    def _get_invalid_menu_message(self) -> str:
        """Return invalid menu selection message"""
        return "\n".join([
            "Invalid option.",
            "",
            "Please choose:",
            "",
            "1 - National KPI",
            "2 - DN Analysis",
            "3 - Dealer Analytics",
            "4 - Warehouse Analytics",
            "5 - Product Analytics",
            "6 - City Analytics",
            "7 - AI Assistant",
            "99 - Return to Main Menu"
        ])
    
    def _get_error_message(self) -> str:
        """Return error message"""
        return "\n".join([
            "⚠️ The selected module encountered an error.",
            "",
            "Please try again.",
            "",
            "Reply 99 to return to the main menu."
        ])
    
    def _get_service_unavailable_message(self, service_name: str) -> str:
        """Return service unavailable message"""
        return "\n".join([
            f"⚠️ {service_name} is currently unavailable.",
            "",
            "Please select another option or try again later.",
            "",
            "Reply 99 to return to the main menu."
        ])
    
    def _is_menu_selection(self, message: str) -> bool:
        """Check if message is a valid menu selection"""
        return message in ["1", "2", "3", "4", "5", "6", "7", "99"]
    
    # ============================================================
    # UNLOCKED SESSION HANDLER
    # ============================================================
    
    async def _handle_unlocked_session(self, message: str, phone: str, session: SessionData) -> str:
        """
        Handle messages when session is NOT locked.
        Shows menu or routes selection.
        """
        # Check if it's a valid menu selection
        if self._is_menu_selection(message):
            # Check for 99 - unlock/return to menu
            if message == "99":
                await self._unlock_session(phone)
                return self._get_main_menu()
            
            # Valid selection 1-7 - lock and route
            service_name = self._service_names.get(message, "Unknown")
            logger.info(f"🎯 Menu selection: {message} -> {service_name}")
            
            # Check if service is available
            if not self._is_service_available(message):
                return self._get_service_unavailable_message(service_name)
            
            # Lock session
            await self._lock_session(phone, message)
            
            # Forward to service
            return await self._forward_to_service(message, message, phone)
        
        # Invalid input - show menu
        logger.info(f"❌ Invalid menu input: '{message}' from {phone}")
        return self._get_invalid_menu_message()
    
    # ============================================================
    # LOCKED SESSION HANDLER - FORWARD EVERYTHING
    # ============================================================
    
    async def _forward_to_locked_service(self, message: str, session: SessionData) -> str:
        """
        Forward message to locked service.
        NO ROUTING, NO MENU, NO AI, NO INTENT DETECTION.
        """
        # Check for unlock command (99)
        if message == "99":
            # Unlock and show menu
            await self._unlock_session(session.phone)
            logger.info(f"🔓 Unlocked via 99 for {session.phone}")
            return self._get_main_menu()
        
        # Forward to locked service
        service_key = self._get_service_key(session.locked_service)
        if not service_key:
            # Should not happen, but just in case
            await self._unlock_session(session.phone)
            return self._get_main_menu()
        
        logger.info(f"🔄 Forwarding to {session.locked_service} for {session.phone}")
        return await self._forward_to_service(service_key, message, session.phone)
    
    # ============================================================
    # ROUTER - FORWARD TO SERVICES
    # ============================================================
    
    async def _forward_to_service(self, service_key: str, message: str, phone: str) -> str:
        """
        Forward message to the appropriate service.
        PURE ROUTING - NO BUSINESS LOGIC.
        """
        try:
            handler = self._service_handlers.get(service_key)
            if not handler:
                logger.error(f"❌ No handler for service key: {service_key}")
                return self._get_error_message()
            
            # Call the service handler
            response = await handler(message, phone)
            
            # Check for service exit signal
            if response == EXIT_SIGNAL:
                await self._unlock_session(phone)
                return self._get_main_menu()
            
            return response
            
        except Exception as e:
            logger.error(f"❌ Service error for {service_key}: {e}")
            logger.error(traceback.format_exc())
            return self._get_error_message()
    
    def _get_service_key(self, service_name: str) -> Optional[str]:
        """Get service key from service name"""
        for key, name in self._service_names.items():
            if name == service_name:
                return key
        return None
    
    def _is_service_available(self, service_key: str) -> bool:
        """Check if a service is available"""
        availability = {
            "1": KPI_AVAILABLE,
            "2": DN_AVAILABLE,
            "3": DEALER_AVAILABLE,
            "4": WAREHOUSE_AVAILABLE,
            "5": PRODUCT_AVAILABLE,
            "6": CITY_AVAILABLE,
            "7": GROQ_AVAILABLE,
        }
        return availability.get(service_key, False)
    
    # ============================================================
    # SERVICE HANDLERS - PURE FORWARDING
    # ============================================================
    
    async def _handle_kpi(self, message: str, phone: str) -> str:
        """Forward to National KPI Service"""
        if not KPI_AVAILABLE or get_kpi_service is None:
            return self._get_service_unavailable_message("National KPI")
        
        service = get_kpi_service()
        if hasattr(service, 'process_query'):
            return service.process_query(message)
        elif hasattr(service, 'get_kpi_dashboard'):
            return service.get_kpi_dashboard()
        else:
            return "📊 National KPI Dashboard\n\nPlease wait while we fetch the data..."
    
    async def _handle_dn(self, message: str, phone: str) -> str:
        """Forward to DN Analysis Service"""
        if not DN_AVAILABLE or get_dn_analysis_service is None:
            return self._get_service_unavailable_message("DN Analysis")
        
        service = get_dn_analysis_service()
        if hasattr(service, 'process_query'):
            return service.process_query(message)
        elif hasattr(service, 'analyze_dn'):
            # Extract DN number if needed
            import re
            dn_match = re.search(r'[A-Za-z0-9\-]{6,}', message)
            if dn_match:
                return service.analyze_dn(dn_match.group())
            else:
                return "📦 Please provide a valid Delivery Note number.\n\nExample: DN-12345"
        else:
            return "📦 DN Analysis\n\nPlease provide a Delivery Note number to track."
    
    async def _handle_dealer(self, message: str, phone: str) -> str:
        """Forward to Dealer Analytics Service"""
        if not DEALER_AVAILABLE or get_dealer_service is None:
            return self._get_service_unavailable_message("Dealer Analytics")
        
        service = get_dealer_service()
        if hasattr(service, 'process_whatsapp_query'):
            return service.process_whatsapp_query(message, phone)
        else:
            return "👤 Dealer Analytics\n\nPlease enter a dealer name to search."
    
    async def _handle_warehouse(self, message: str, phone: str) -> str:
        """Forward to Warehouse Service"""
        if not WAREHOUSE_AVAILABLE or get_warehouse_service is None:
            return self._get_service_unavailable_message("Warehouse Analytics")
        
        service = get_warehouse_service()
        if hasattr(service, 'process_query'):
            return service.process_query(message)
        elif hasattr(service, 'get_warehouse_dashboard'):
            return service.get_warehouse_dashboard()
        else:
            return "🏭 Warehouse Analytics\n\nPlease wait while we fetch the data..."
    
    async def _handle_product(self, message: str, phone: str) -> str:
        """Forward to Product Service"""
        if not PRODUCT_AVAILABLE or get_product_service is None:
            return self._get_service_unavailable_message("Product Analytics")
        
        service = get_product_service()
        if hasattr(service, 'process_query'):
            return service.process_query(message)
        elif hasattr(service, 'get_product_analytics'):
            return service.get_product_analytics()
        else:
            return "📦 Product Analytics\n\nPlease wait while we fetch the data..."
    
    async def _handle_city(self, message: str, phone: str) -> str:
        """Forward to City Service"""
        if not CITY_AVAILABLE or get_city_service is None:
            return self._get_service_unavailable_message("City Analytics")
        
        service = get_city_service()
        if hasattr(service, 'process_query'):
            return service.process_query(message)
        elif hasattr(service, 'get_city_analytics'):
            return service.get_city_analytics()
        else:
            return "📍 City Analytics\n\nPlease wait while we fetch the data..."
    
    async def _handle_groq(self, message: str, phone: str) -> str:
        """Forward to Groq AI Service"""
        if not GROQ_AVAILABLE or get_groq_service is None:
            return self._get_service_unavailable_message("AI Assistant")
        
        service = get_groq_service()
        if hasattr(service, 'process_query'):
            return service.process_query(message)
        elif hasattr(service, 'generate_response'):
            return service.generate_response(message)
        else:
            return "🤖 AI Assistant\n\nHow can I help you today?"
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check - no business logic, only status"""
        uptime = (datetime.now() - self._startup).seconds
        
        # Count active sessions
        active_sessions = 0
        locked_sessions = 0
        expired_sessions = 0
        
        for phone, session in self._sessions.items():
            if session.is_expired():
                expired_sessions += 1
            else:
                active_sessions += 1
                if session.locked:
                    locked_sessions += 1
        
        return {
            "status": "healthy",
            "version": self._version,
            "uptime_seconds": uptime,
            "uptime_display": f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s",
            "active_sessions": active_sessions,
            "locked_sessions": locked_sessions,
            "expired_sessions": expired_sessions,
            "services_available": {
                "kpi": KPI_AVAILABLE,
                "dn": DN_AVAILABLE,
                "dealer": DEALER_AVAILABLE,
                "warehouse": WAREHOUSE_AVAILABLE,
                "product": PRODUCT_AVAILABLE,
                "city": CITY_AVAILABLE,
                "groq": GROQ_AVAILABLE,
            },
            "started_at": self._startup.isoformat()
        }

# ============================================================
# SINGLETON AND EXPORTS - DO NOT CHANGE
# ============================================================

_service_instance: Optional[AIProviderService] = None

def get_ai_provider_service() -> AIProviderService:
    """Get singleton instance"""
    global _service_instance
    if _service_instance is None:
        _service_instance = AIProviderService()
    return _service_instance

# ============================================================
# WEBHOOK ENTRY POINT - DO NOT CHANGE SIGNATURE
# ============================================================

async def process_whatsapp_query(message: str, sender: str) -> str:
    """
    Main entry point for WhatsApp queries.
    
    DO NOT CHANGE:
    - Function name
    - Parameters
    - Return type
    
    This is called by webhook.py.
    
    Args:
        message: User's message
        sender: Sender's phone number
        
    Returns:
        Response string
    """
    service = get_ai_provider_service()
    return await service.process_whatsapp_query(message, sender)

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "AIProviderService",
    "get_ai_provider_service",
    "process_whatsapp_query",
    "VERSION"
]

# ============================================================
# TEST MODE - PURE ROUTER TEST
# ============================================================

if __name__ == "__main__":
    import asyncio
    
    print("\n" + "=" * 70)
    print("AI PROVIDER GATEWAY v{} - TEST MODE".center(70).format(VERSION))
    print("=" * 70)
    print()
    print("🚀 This is a PURE ROUTER - NO BUSINESS LOGIC")
    print("   It ONLY routes messages to services")
    print()
    
    service = get_ai_provider_service()
    
    # Health check
    health = service.health_check()
    print("📊 HEALTH CHECK:")
    print("-" * 40)
    for key, value in health.items():
        if key != 'services_available':
            print(f"  {key}: {value}")
    print("-" * 40)
    print()
    
    # Interactive test
    async def test_loop():
        print("🔍 Enter '99' anytime to return to main menu")
        print("=" * 70)
        print()
        
        while True:
            try:
                query = input("👤 You: ").strip()
                
                if query.lower() in ['exit', 'quit']:
                    print("\n👋 Goodbye!")
                    break
                
                if not query:
                    continue
                
                print("\n⏳ Routing...\n")
                response = await service.process_whatsapp_query(query, "test_user")
                print(response)
                print()
                print("-" * 70)
                print()
                
            except KeyboardInterrupt:
                print("\n\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"\n❌ Error: {e}\n")
                import traceback
                traceback.print_exc()
    
    asyncio.run(test_loop())
