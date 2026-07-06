#!/usr/bin/env python3
# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 54.0 - CLEAN MENU (ONLY WORKING SERVICES)
# ============================================================

"""
================================================================================
AI PROVIDER SERVICE - PURE GATEWAY & SESSION MANAGER
================================================================================

This file is ONLY the Gateway, Router, Menu Controller, and Session Manager.

CRITICAL BEHAVIOR:
1. ONLY working services appear in the menu
2. Non-working services are SILENTLY ignored (no errors shown)
3. The menu ALWAYS displays cleanly
4. Users ONLY see services that are available
5. NO "Invalid option" errors - just show the menu
6. The gateway ALWAYS returns the menu or a response

================================================================================
"""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Optional, Dict, Any, List
from datetime import datetime

# ============================================================
# LOGGING SETUP
# ============================================================

logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTS
# ============================================================

VERSION = "54.0"
EXIT_SIGNAL = "__EXIT__"
SESSION_TIMEOUT_SECONDS = 1800  # 30 minutes

# ============================================================
# SERVICE LOADING - SILENT FAILURES
# ============================================================

# Each service is loaded silently. If it fails, it's simply not added.
# NO ERRORS are shown to users.

WORKING_SERVICES = {}

# 1. National KPI Service
try:
    from app.services.national_kpi_service import get_kpi_service
    service = get_kpi_service()
    if service:
        WORKING_SERVICES["1"] = {
            "id": "1",
            "name": "National KPI",
            "instance": service
        }
        logger.info("✅ National KPI Service loaded")
except Exception as e:
    # SILENT FAIL - service not added to menu
    logger.warning(f"⚠️ National KPI Service skipped: {e}")

# 2. DN Analysis Service
try:
    from app.services.dn_analysis import get_dn_analysis_service
    service = get_dn_analysis_service()
    if service:
        WORKING_SERVICES["2"] = {
            "id": "2",
            "name": "DN Analysis",
            "instance": service
        }
        logger.info("✅ DN Analysis Service loaded")
except Exception as e:
    # SILENT FAIL - service not added to menu
    logger.warning(f"⚠️ DN Analysis Service skipped: {e}")

# 3. Dealer Analytics Service
try:
    from app.services.dealer_analytics_service import get_dealer_service
    service = get_dealer_service()
    if service:
        WORKING_SERVICES["3"] = {
            "id": "3",
            "name": "Dealer Analytics",
            "instance": service
        }
        logger.info("✅ Dealer Analytics Service loaded")
except Exception as e:
    # SILENT FAIL - service not added to menu
    logger.warning(f"⚠️ Dealer Analytics Service skipped: {e}")

# 4. Warehouse Service
try:
    from app.services.warehouse_service import get_warehouse_analytics_service
    service = get_warehouse_analytics_service()
    if service:
        WORKING_SERVICES["4"] = {
            "id": "4",
            "name": "Warehouse Analytics",
            "instance": service
        }
        logger.info("✅ Warehouse Service loaded")
except Exception as e:
    # SILENT FAIL - service not added to menu
    logger.warning(f"⚠️ Warehouse Service skipped: {e}")

# 5. Product Service
try:
    from app.services.product_service import get_product_analytics_service
    service = get_product_analytics_service()
    if service:
        WORKING_SERVICES["5"] = {
            "id": "5",
            "name": "Product Analytics",
            "instance": service
        }
        logger.info("✅ Product Service loaded")
except Exception as e:
    # SILENT FAIL - service not added to menu
    logger.warning(f"⚠️ Product Service skipped: {e}")

# 6. City Service
try:
    from app.services.city_service import get_city_analytics_service
    service = get_city_analytics_service()
    if service:
        WORKING_SERVICES["6"] = {
            "id": "6",
            "name": "City Analytics",
            "instance": service
        }
        logger.info("✅ City Service loaded")
except Exception as e:
    # SILENT FAIL - service not added to menu
    logger.warning(f"⚠️ City Service skipped: {e}")

# 7. Groq AI Service
try:
    from app.services.groq_service import get_groq_service
    service = get_groq_service()
    if service:
        WORKING_SERVICES["7"] = {
            "id": "7",
            "name": "AI Assistant",
            "instance": service
        }
        logger.info("✅ AI Assistant Service loaded")
except Exception as e:
    # SILENT FAIL - service not added to menu
    logger.warning(f"⚠️ AI Assistant Service skipped: {e}")

# ============================================================
# SESSION DATA CLASS - LIGHTWEIGHT
# ============================================================

class SessionData:
    """Lightweight user session data - pure session state only"""
    
    def __init__(self, phone: str):
        self.phone: str = phone
        self.locked: bool = False
        self.locked_service_id: Optional[str] = None
        self.locked_service_name: Optional[str] = None
        self.created_at: datetime = datetime.now()
        self.last_activity: datetime = datetime.now()
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.now()
    
    def is_expired(self) -> bool:
        """Check if session has expired (30 minutes timeout)"""
        elapsed = (datetime.now() - self.last_activity).total_seconds()
        return elapsed > SESSION_TIMEOUT_SECONDS
    
    def lock(self, service_id: str, service_name: str):
        """Lock session to a specific service"""
        self.locked = True
        self.locked_service_id = service_id
        self.locked_service_name = service_name
        self.update_activity()
    
    def unlock(self):
        """Unlock session"""
        self.locked = False
        self.locked_service_id = None
        self.locked_service_name = None
        self.update_activity()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dict for logging"""
        return {
            "phone": self.phone,
            "locked": self.locked,
            "locked_service_id": self.locked_service_id,
            "locked_service_name": self.locked_service_name,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "is_expired": self.is_expired()
        }

# ============================================================
# MAIN GATEWAY SERVICE
# ============================================================

class AIProviderService:
    """
    PURE Gateway, Router, Menu Controller, and Session Manager.
    
    This class does NOT contain any business logic, SQL, AI, or analytics.
    It ONLY routes messages to the appropriate services.
    
    CRITICAL:
    - ONLY working services appear in the menu
    - Non-working services are silently ignored
    - The menu ALWAYS displays cleanly
    - EVERY message gets a reply
    - NO "Invalid option" - just show the menu
    """
    
    _instance: Optional["AIProviderService"] = None
    _sessions: Dict[str, SessionData] = {}
    _lock = threading.RLock()
    
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
        self._total_requests = 0
        self._successful_requests = 0
        self._error_count = 0
        
        self._show_startup()
    
    def _show_startup(self):
        """Display startup information - only shows working services"""
        print("\n" + "=" * 70)
        print(f"🤖 AI PROVIDER GATEWAY v{self._version}".center(70))
        print("=" * 70)
        print("📋 WORKING SERVICES:")
        print("-" * 70)
        
        if not WORKING_SERVICES:
            print("  ⚠️ No services available - only menu will be shown")
        else:
            for key, svc in WORKING_SERVICES.items():
                print(f"  {key}. ✅ {svc['name']}")
        
        print("-" * 70)
        print(f"  ✅ Working: {len(WORKING_SERVICES)} services")
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
            Response string - ALWAYS returns a string, never None
        """
        self._total_requests += 1
        
        try:
            logger.info(f"📨 Incoming: '{message}' from {sender}")
            
            # Handle empty message
            if not message or not message.strip():
                return self._get_main_menu()
            
            msg = message.strip()
            
            # Get or create session
            session = self._get_or_create_session(sender)
            
            # Check if session is expired
            if session.is_expired():
                logger.info(f"⏰ Session expired for {sender}")
                self._clear_session(sender)
                return self._get_main_menu()
            
            # Update activity timestamp
            session.update_activity()
            
            # Check if session is locked
            if session.locked:
                # Session is locked - forward directly to locked service
                logger.info(f"🔒 Session locked to {session.locked_service_name} for {sender}")
                response = await self._forward_to_locked_service(msg, session)
                return response
            
            # Session is NOT locked - handle menu or routing
            return await self._handle_unlocked_session(msg, sender, session)
            
        except Exception as e:
            # CRITICAL: ALWAYS return a response, never let an exception escape
            self._error_count += 1
            logger.error(f"❌ Fatal error: {e}")
            logger.error(traceback.format_exc())
            return self._get_main_menu()  # Always show menu on error
    
    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================
    
    def _get_or_create_session(self, phone: str) -> SessionData:
        """Get or create session for phone number"""
        with self._lock:
            if phone in self._sessions:
                session = self._sessions[phone]
                if session.is_expired():
                    logger.info(f"⏰ Session expired for {phone}, creating new")
                    del self._sessions[phone]
                    session = SessionData(phone)
                    self._sessions[phone] = session
                return session
            
            session = SessionData(phone)
            self._sessions[phone] = session
            logger.info(f"🆕 New session created for {phone}")
            return session
    
    def _lock_session(self, phone: str, service_id: str) -> Optional[SessionData]:
        """Lock session to a specific service"""
        with self._lock:
            if phone not in self._sessions:
                return None
            
            session = self._sessions[phone]
            service = WORKING_SERVICES.get(service_id)
            if not service:
                return None
            
            session.lock(service_id, service["name"])
            logger.info(f"🔒 Session locked to {service['name']} for {phone}")
            return session
    
    def _unlock_session(self, phone: str) -> Optional[SessionData]:
        """Unlock session"""
        with self._lock:
            if phone not in self._sessions:
                return None
            
            session = self._sessions[phone]
            if session.locked:
                logger.info(f"🔓 Session unlocked for {phone} (was {session.locked_service_name})")
            session.unlock()
            return session
    
    def _clear_session(self, phone: str) -> bool:
        """Clear session"""
        with self._lock:
            if phone in self._sessions:
                del self._sessions[phone]
                logger.info(f"🧹 Session cleared for {phone}")
                return True
            return False
    
    # ============================================================
    # MENU CONTROLLER - CLEAN, ONLY WORKING SERVICES
    # ============================================================
    
    def _get_main_menu(self) -> str:
        """Return the main menu with ONLY working services - NO errors"""
        lines = []
        
        if not WORKING_SERVICES:
            # No services available - show friendly message
            lines = [
                "📦 Logistics Intelligence Center"
                "",
                "⚠️ No services are currently available.",
                "",
                "Please try again later.",
                "",
                "99 - Return to Main Menu"
            ]
            return "\n".join(lines)
        
        # Build clean menu
        lines.append("📦 DN INTELLIGENCE CENTER")
        lines.append("")
        lines.append("Please choose from:")
        lines.append("")
        
        # Show each working service
        for key, svc in WORKING_SERVICES.items():
            lines.append(f"{key} - {svc['name']}")
        
        lines.append("")
        lines.append("99 - Return to Main Menu")
        
        return "\n".join(lines)
    
    # ============================================================
    # UNLOCKED SESSION HANDLER - NO "Invalid option"
    # ============================================================
    
    async def _handle_unlocked_session(self, message: str, phone: str, session: SessionData) -> str:
        """
        Handle messages when session is NOT locked.
        Shows menu or routes selection.
        ALWAYS returns the menu for invalid input - NO errors.
        """
        try:
            # Check if it's a valid menu selection
            if message in WORKING_SERVICES:
                # Valid selection - lock and route
                service = WORKING_SERVICES[message]
                logger.info(f"🎯 Menu selection: {message} -> {service['name']}")
                
                # Lock session
                locked_session = self._lock_session(phone, message)
                if not locked_session:
                    return self._get_main_menu()
                
                # Get initial response from service
                return await self._forward_to_service(message, message, phone)
            
            # Check for 99 - return to menu
            if message == "99":
                self._unlock_session(phone)
                return self._get_main_menu()
            
            # ANY invalid input → ALWAYS show the menu (NO error messages)
            logger.info(f"ℹ️ Invalid input: '{message}' from {phone} - showing menu")
            return self._get_main_menu()
            
        except Exception as e:
            logger.error(f"❌ Error in _handle_unlocked_session: {e}")
            logger.error(traceback.format_exc())
            return self._get_main_menu()  # Always show menu on error
    
    # ============================================================
    # LOCKED SESSION HANDLER - FORWARD EVERYTHING
    # ============================================================
    
    async def _forward_to_locked_service(self, message: str, session: SessionData) -> str:
        """
        Forward message to locked service.
        NO ROUTING, NO MENU, NO AI, NO INTENT DETECTION.
        ALWAYS returns a string.
        """
        try:
            # Check for unlock command (99)
            if message == "99":
                self._unlock_session(session.phone)
                logger.info(f"🔓 Unlocked via 99 for {session.phone}")
                return self._get_main_menu()
            
            # Forward to locked service
            if not session.locked_service_id:
                self._unlock_session(session.phone)
                return self._get_main_menu()
            
            service_id = session.locked_service_id
            logger.info(f"🔄 Forwarding to {session.locked_service_name} for {session.phone}")
            return await self._forward_to_service(service_id, message, session.phone)
            
        except Exception as e:
            logger.error(f"❌ Error in _forward_to_locked_service: {e}")
            logger.error(traceback.format_exc())
            return self._get_main_menu()  # Always show menu on error
    
    # ============================================================
    # ROUTER - FORWARD TO SERVICES
    # ============================================================
    
    async def _forward_to_service(self, service_id: str, message: str, phone: str) -> str:
        """
        Forward message to the appropriate service.
        PURE ROUTING - NO BUSINESS LOGIC.
        ALWAYS returns a string.
        """
        try:
            service = WORKING_SERVICES.get(service_id)
            if not service:
                return self._get_main_menu()
            
            service_instance = service["instance"]
            
            if not service_instance:
                return self._get_main_menu()
            
            # Try to call the service
            response = None
            
            try:
                # Try handle_message (async)
                if hasattr(service_instance, 'handle_message') and callable(service_instance.handle_message):
                    if hasattr(service_instance.handle_message, '__await__'):
                        response = await service_instance.handle_message(message, phone)
                    else:
                        response = service_instance.handle_message(message, phone)
                
                # Try process_whatsapp_query
                elif hasattr(service_instance, 'process_whatsapp_query') and callable(service_instance.process_whatsapp_query):
                    response = service_instance.process_whatsapp_query(message, phone)
                
                # Try process_query
                elif hasattr(service_instance, 'process_query') and callable(service_instance.process_query):
                    response = service_instance.process_query(message)
                
                # Try get_main_menu
                elif hasattr(service_instance, 'get_main_menu') and callable(service_instance.get_main_menu):
                    response = service_instance.get_main_menu()
                
                else:
                    # Last resort: service doesn't have a known method
                    response = f"📊 {service['name']}\n\nService is available. Please enter your query."
                    
            except Exception as service_error:
                # Catch ANY exception from the service
                logger.error(f"❌ Service {service['name']} error: {service_error}")
                logger.error(traceback.format_exc())
                return self._get_main_menu()  # Show menu on service error
            
            # Ensure we have a string response
            if response is None:
                response = self._get_main_menu()
            elif not isinstance(response, str):
                response = str(response)
            
            # Check for exit signal
            if response == EXIT_SIGNAL or response == "99":
                self._unlock_session(phone)
                return self._get_main_menu()
            
            # Track success
            self._successful_requests += 1
            return response
            
        except Exception as e:
            logger.error(f"❌ Fatal error in _forward_to_service: {e}")
            logger.error(traceback.format_exc())
            return self._get_main_menu()  # Always show menu on error
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check - shows which services are working"""
        uptime = (datetime.now() - self._startup).seconds
        
        # Count active sessions
        active_sessions = 0
        locked_sessions = 0
        expired_sessions = 0
        
        with self._lock:
            for phone, session in self._sessions.items():
                if session.is_expired():
                    expired_sessions += 1
                else:
                    active_sessions += 1
                    if session.locked:
                        locked_sessions += 1
        
        return {
            "status": "healthy" if WORKING_SERVICES else "degraded",
            "version": self._version,
            "uptime_seconds": uptime,
            "uptime_display": f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s",
            "active_sessions": active_sessions,
            "locked_sessions": locked_sessions,
            "expired_sessions": expired_sessions,
            "total_requests": self._total_requests,
            "successful_requests": self._successful_requests,
            "error_count": self._error_count,
            "success_rate": round((self._successful_requests / max(self._total_requests, 1)) * 100, 1),
            "working_services": list(WORKING_SERVICES.keys()),
            "working_services_count": len(WORKING_SERVICES),
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
        Response string - ALWAYS returns a string, never None
    """
    try:
        service = get_ai_provider_service()
        return await service.process_whatsapp_query(message, sender)
    except Exception as e:
        # CRITICAL: ALWAYS return a response, never let an exception escape
        logger.error(f"❌ Fatal error in process_whatsapp_query: {e}")
        logger.error(traceback.format_exc())
        return "\n".join([
            "📦 DN INTELLIGENCE CENTER",
            "",
            "⚠️ Service is temporarily unavailable.",
            "",
            "Please try again later.",
            "",
            "99 - Return to Main Menu"
        ])

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "AIProviderService",
    "get_ai_provider_service",
    "process_whatsapp_query",
    "VERSION",
    "WORKING_SERVICES"
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
        print(f"  {key}: {value}")
    print("-" * 40)
    print()
    
    # Show working services
    print("📋 WORKING SERVICES:")
    if WORKING_SERVICES:
        for key, svc in WORKING_SERVICES.items():
            print(f"  {key}. ✅ {svc['name']}")
    else:
        print("  ⚠️ No services available")
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
                traceback.print_exc()
    
    asyncio.run(test_loop())
