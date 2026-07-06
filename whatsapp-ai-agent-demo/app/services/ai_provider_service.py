#!/usr/bin/env python3
# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 51.0 - PURE GATEWAY WITH SERVICE STATUS DISPLAY
# ============================================================

"""
================================================================================
AI PROVIDER SERVICE - PURE GATEWAY & SESSION MANAGER
================================================================================

This file is ONLY the Gateway, Router, Menu Controller, and Session Manager.

It shows:
- ✅ Which services are working (green check)
- ❌ Which services are NOT working (red X)

It ONLY:
- Receives WhatsApp messages from webhook.py
- Manages sessions
- Shows Main Menu with service status
- Locks selected modules (only if working)
- Forwards messages to working services
- Unlocks on 99

Services:
    1 → National KPI Service      (✅ Working / ❌ Not Working)
    2 → DN Analysis Service       (✅ Working / ❌ Not Working)
    3 → Dealer Analytics Service  (✅ Working / ❌ Not Working)
    4 → Warehouse Service         (✅ Working / ❌ Not Working)
    5 → Product Service           (✅ Working / ❌ Not Working)
    6 → City Service              (✅ Working / ❌ Not Working)
    7 → AI Assistant (Groq)       (✅ Working / ❌ Not Working)
================================================================================
"""

from __future__ import annotations

import logging
import threading
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTS
# ============================================================

VERSION = "51.0"
EXIT_SIGNAL = "__EXIT__"
SESSION_TIMEOUT_SECONDS = 1800  # 30 minutes

# ============================================================
# SERVICE IMPORTS - PUBLIC ENTRY FUNCTIONS ONLY
# ============================================================

# Track which services are available
SERVICE_STATUS = {}

# 1. National KPI Service
try:
    from app.services.national_kpi_service import get_kpi_service
    SERVICE_STATUS["1"] = {"name": "National KPI", "available": True, "getter": get_kpi_service}
    logger.info("✅ National KPI Service loaded")
except ImportError as e:
    SERVICE_STATUS["1"] = {"name": "National KPI", "available": False, "getter": None, "error": str(e)}
    logger.warning(f"⚠️ National KPI Service not available: {e}")

# 2. DN Analysis Service
try:
    from app.services.dn_analysis import get_dn_analysis_service
    SERVICE_STATUS["2"] = {"name": "DN Analysis", "available": True, "getter": get_dn_analysis_service}
    logger.info("✅ DN Analysis Service loaded")
except ImportError as e:
    SERVICE_STATUS["2"] = {"name": "DN Analysis", "available": False, "getter": None, "error": str(e)}
    logger.warning(f"⚠️ DN Analysis Service not available: {e}")

# 3. Dealer Analytics Service
try:
    from app.services.dealer_analytics_service import get_dealer_service, EXIT_SIGNAL
    SERVICE_STATUS["3"] = {"name": "Dealer Analytics", "available": True, "getter": get_dealer_service}
    logger.info("✅ Dealer Analytics Service loaded")
except ImportError as e:
    SERVICE_STATUS["3"] = {"name": "Dealer Analytics", "available": False, "getter": None, "error": str(e)}
    logger.warning(f"⚠️ Dealer Analytics Service not available: {e}")

# 4. Warehouse Service
try:
    from app.services.warehouse_service import get_warehouse_analytics_service
    SERVICE_STATUS["4"] = {"name": "Warehouse Analytics", "available": True, "getter": get_warehouse_analytics_service}
    logger.info("✅ Warehouse Service loaded")
except ImportError as e:
    SERVICE_STATUS["4"] = {"name": "Warehouse Analytics", "available": False, "getter": None, "error": str(e)}
    logger.warning(f"⚠️ Warehouse Service not available: {e}")

# 5. Product Service
try:
    from app.services.product_service import get_product_analytics_service
    SERVICE_STATUS["5"] = {"name": "Product Analytics", "available": True, "getter": get_product_analytics_service}
    logger.info("✅ Product Service loaded")
except ImportError as e:
    SERVICE_STATUS["5"] = {"name": "Product Analytics", "available": False, "getter": None, "error": str(e)}
    logger.warning(f"⚠️ Product Service not available: {e}")

# 6. City Service
try:
    from app.services.city_service import get_city_analytics_service
    SERVICE_STATUS["6"] = {"name": "City Analytics", "available": True, "getter": get_city_analytics_service}
    logger.info("✅ City Service loaded")
except ImportError as e:
    SERVICE_STATUS["6"] = {"name": "City Analytics", "available": False, "getter": None, "error": str(e)}
    logger.warning(f"⚠️ City Service not available: {e}")

# 7. Groq AI Service
try:
    from app.services.groq_service import get_groq_service
    SERVICE_STATUS["7"] = {"name": "AI Assistant", "available": True, "getter": get_groq_service}
    logger.info("✅ Groq AI Service loaded")
except ImportError as e:
    SERVICE_STATUS["7"] = {"name": "AI Assistant", "available": False, "getter": None, "error": str(e)}
    logger.warning(f"⚠️ Groq AI Service not available: {e}")

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
        self.service_instance: Optional[Any] = None
        self.menu_id: Optional[int] = None
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.now()
    
    def is_expired(self) -> bool:
        """Check if session has expired (30 minutes timeout)"""
        elapsed = (datetime.now() - self.last_activity).total_seconds()
        return elapsed > SESSION_TIMEOUT_SECONDS
    
    def lock(self, service_name: str, service_instance: Any, menu_id: int):
        """Lock session to a specific service"""
        self.locked = True
        self.locked_service = service_name
        self.service_instance = service_instance
        self.menu_id = menu_id
        self.update_activity()
    
    def unlock(self):
        """Unlock session"""
        self.locked = False
        self.locked_service = None
        self.service_instance = None
        self.menu_id = None
        self.update_activity()

# ============================================================
# MAIN GATEWAY SERVICE
# ============================================================

class AIProviderService:
    """
    PURE Gateway, Router, Menu Controller, and Session Manager.
    
    This class does NOT contain any business logic, SQL, AI, or analytics.
    It ONLY routes messages to the appropriate services.
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
        
        self._show_startup()
    
    def _show_startup(self):
        """Display startup information with service status"""
        print("\n" + "=" * 70)
        print(f"🤖 AI PROVIDER GATEWAY v{self._version} - PURE ROUTER".center(70))
        print("=" * 70)
        print("📋 SERVICES STATUS:")
        print("-" * 70)
        
        # Count working services
        working_count = 0
        total_count = 0
        
        for key, svc in SERVICE_STATUS.items():
            total_count += 1
            if svc["available"]:
                working_count += 1
                status = "✅ WORKING"
            else:
                status = "❌ NOT WORKING"
                error = svc.get("error", "Unknown error")
                if "get_kpi_service" in error:
                    error = "Missing get_kpi_service() function"
                elif "get_national_kpi_service" in error:
                    error = "Missing get_national_kpi_service() function"
            
            print(f"  {key}. {status}  {svc['name']}")
            if not svc["available"]:
                print(f"     Error: {error[:80]}...")
        
        print("-" * 70)
        print(f"  ✅ Working: {working_count}/{total_count} services")
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
        self._total_requests += 1
        
        try:
            logger.info(f"📨 Incoming: '{message}' from {sender}")
            
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
                logger.info(f"🔒 Session locked to {session.locked_service} for {sender}")
                response = await self._forward_to_locked_service(msg, session)
                return response
            
            # Session is NOT locked - handle menu or routing
            return await self._handle_unlocked_session(msg, sender, session)
            
        except Exception as e:
            logger.error(f"❌ Error in process_whatsapp_query: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self._get_error_message()
    
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
    
    def _lock_session(self, phone: str, menu_id: str) -> Optional[SessionData]:
        """Lock session to a specific service"""
        with self._lock:
            if phone not in self._sessions:
                return None
            
            session = self._sessions[phone]
            service = SERVICE_STATUS.get(menu_id)
            if not service or not service["available"]:
                return None
            
            service_instance = service["getter"]()
            if not service_instance:
                return None
            
            session.lock(service["name"], service_instance, int(menu_id))
            logger.info(f"🔒 Session locked to {service['name']} for {phone}")
            return session
    
    def _unlock_session(self, phone: str) -> Optional[SessionData]:
        """Unlock session"""
        with self._lock:
            if phone not in self._sessions:
                return None
            
            session = self._sessions[phone]
            if session.locked:
                logger.info(f"🔓 Session unlocked for {phone} (was {session.locked_service})")
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
    # MENU CONTROLLER
    # ============================================================
    
    def _get_main_menu(self) -> str:
        """Return the exact main menu with service status"""
        lines = [
            "📦 DN INTELLIGENCE CENTER",
            "",
        ]
        
        # Show each service with status
        for key, svc in SERVICE_STATUS.items():
            if svc["available"]:
                status = "✅"
            else:
                status = "❌"
            lines.append(f"{key}. {status} {svc['name']}")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "Reply with:",
            "",
        ])
        
        for key, svc in SERVICE_STATUS.items():
            lines.append(f"{key} - {svc['name']}")
        
        lines.extend([
            "99 - Return to Main Menu",
            "",
            "📌 Services with ✅ are working",
            "📌 Services with ❌ are currently unavailable"
        ])
        
        return "\n".join(lines)
    
    def _get_invalid_menu_message(self) -> str:
        """Return invalid menu selection message"""
        return "\n".join([
            "Invalid option.",
            "",
            "Please choose a working service:",
            "",
        ] + [
            f"{key} - {svc['name']} {'✅' if svc['available'] else '❌'}"
            for key, svc in SERVICE_STATUS.items()
        ] + [
            "",
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
            f"❌ {service_name} is currently unavailable.",
            "",
            "Please select another service or try again later.",
            "",
            "Reply 99 to return to the main menu."
        ])
    
    def _is_menu_selection(self, message: str) -> bool:
        """Check if message is a valid menu selection"""
        return message in list(SERVICE_STATUS.keys()) + ["99"]
    
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
            service = SERVICE_STATUS.get(message)
            if not service:
                return self._get_invalid_menu_message()
            
            # Check if service is available
            if not service["available"]:
                return self._get_service_unavailable_message(service["name"])
            
            logger.info(f"🎯 Menu selection: {message} -> {service['name']}")
            
            # Lock session
            locked_session = self._lock_session(phone, message)
            if not locked_session:
                return self._get_service_unavailable_message(service["name"])
            
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
            self._unlock_session(session.phone)
            logger.info(f"🔓 Unlocked via 99 for {session.phone}")
            return self._get_main_menu()
        
        # Forward to locked service
        if not session.menu_id:
            self._unlock_session(session.phone)
            return self._get_main_menu()
        
        menu_id = str(session.menu_id)
        logger.info(f"🔄 Forwarding to {session.locked_service} for {session.phone}")
        return await self._forward_to_service(menu_id, message, session.phone)
    
    # ============================================================
    # ROUTER - FORWARD TO SERVICES
    # ============================================================
    
    async def _forward_to_service(self, service_key: str, message: str, phone: str) -> str:
        """
        Forward message to the appropriate service.
        PURE ROUTING - NO BUSINESS LOGIC.
        """
        try:
            service = SERVICE_STATUS.get(service_key)
            if not service or not service["available"]:
                return self._get_service_unavailable_message(
                    service["name"] if service else "Service"
                )
            
            # Get service instance (from session if locked, or new)
            session = self._get_or_create_session(phone)
            service_instance = session.service_instance if session.locked else service["getter"]()
            
            if not service_instance:
                return self._get_service_unavailable_message(service["name"])
            
            # Call service handler - check which method exists
            response = None
            
            if hasattr(service_instance, 'process_whatsapp_query'):
                response = service_instance.process_whatsapp_query(message, phone)
            elif hasattr(service_instance, 'process_query'):
                response = service_instance.process_query(message)
            elif hasattr(service_instance, 'get_kpi_dashboard'):
                response = service_instance.get_kpi_dashboard()
            elif hasattr(service_instance, 'get_main_menu'):
                response = service_instance.get_main_menu()
            else:
                response = f"📊 {service['name']} Service\n\nPlease wait while we fetch the data..."
            
            # Check for exit signal
            if response == EXIT_SIGNAL or response == "99":
                self._unlock_session(phone)
                return self._get_main_menu()
            
            self._successful_requests += 1
            return response
            
        except Exception as e:
            logger.error(f"❌ Service error for {service_key}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self._get_error_message()
    
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
        
        # Count working services
        working_services = sum(1 for svc in SERVICE_STATUS.values() if svc["available"])
        total_services = len(SERVICE_STATUS)
        
        return {
            "status": "healthy" if working_services > 0 else "degraded",
            "version": self._version,
            "uptime_seconds": uptime,
            "uptime_display": f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s",
            "active_sessions": active_sessions,
            "locked_sessions": locked_sessions,
            "expired_sessions": expired_sessions,
            "total_requests": self._total_requests,
            "successful_requests": self._successful_requests,
            "success_rate": round((self._successful_requests / max(self._total_requests, 1)) * 100, 1),
            "services": {
                key: {
                    "name": svc["name"],
                    "available": svc["available"],
                    "status": "✅ Working" if svc["available"] else "❌ Not Working"
                }
                for key, svc in SERVICE_STATUS.items()
            },
            "services_summary": f"{working_services}/{total_services} services working",
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
    "VERSION",
    "SERVICE_STATUS"
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
        if key != 'services':
            print(f"  {key}: {value}")
    print("-" * 40)
    print()
    
    # Show service status
    print("📋 SERVICE STATUS:")
    for key, svc in SERVICE_STATUS.items():
        status = "✅ WORKING" if svc["available"] else "❌ NOT WORKING"
        print(f"  {key}. {status} - {svc['name']}")
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
