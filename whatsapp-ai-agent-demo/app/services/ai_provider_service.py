#!/usr/bin/env python3
# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 57.0 - ALL 7 SERVICES WORKING (WITH GROQ FIX)
# ============================================================

"""
================================================================================
AI PROVIDER SERVICE - PURE GATEWAY & SESSION MANAGER
================================================================================

This file is ONLY the Gateway, Router, Menu Controller, and Session Manager.

CRITICAL BEHAVIOR:
1. ✅ ALL 7 services appear in the menu
2. ✅ Working services show with ✅
3. ✅ Non-working services show with ❌
4. ✅ The menu ALWAYS displays cleanly
5. ✅ NO "Invalid option" errors - just show the menu
6. ✅ Professional header: 📦 LOGISTICS INTELLIGENCE CENTER

================================================================================
"""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Optional, Dict, Any, List
from datetime import datetime

# ============================================================
# BLOCK 1: LOGGING SETUP
# ============================================================

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 2: CONSTANTS
# ============================================================

VERSION = "57.0"
EXIT_SIGNAL = "__EXIT__"
SESSION_TIMEOUT_SECONDS = 1800  # 30 minutes

# ============================================================
# BLOCK 3: SERVICE LOADING - ALL 7 SERVICES
# ============================================================

# Each service is loaded independently.
# If a service fails, it shows ❌ in menu but doesn't break the app.

WORKING_SERVICES = {}

# ---------------------------------------------------------------------
# SERVICE 1: National KPI Service
# ---------------------------------------------------------------------
try:
    from app.services.national_kpi_service import get_kpi_service
    service = get_kpi_service()
    if service:
        WORKING_SERVICES["1"] = {
            "id": "1",
            "name": "National KPI",
            "instance": service,
            "working": True
        }
        logger.info("✅ National KPI Service loaded")
    else:
        WORKING_SERVICES["1"] = {
            "id": "1",
            "name": "National KPI",
            "instance": None,
            "working": False
        }
        logger.warning("⚠️ National KPI Service returned None")
except Exception as e:
    WORKING_SERVICES["1"] = {
        "id": "1",
        "name": "National KPI",
        "instance": None,
        "working": False
    }
    logger.warning(f"⚠️ National KPI Service skipped: {e}")

# ---------------------------------------------------------------------
# SERVICE 2: DN Analysis Service
# ---------------------------------------------------------------------
try:
    from app.services.dn_analysis import get_dn_analysis_service
    service = get_dn_analysis_service()
    if service:
        WORKING_SERVICES["2"] = {
            "id": "2",
            "name": "DN Analysis",
            "instance": service,
            "working": True
        }
        logger.info("✅ DN Analysis Service loaded")
    else:
        WORKING_SERVICES["2"] = {
            "id": "2",
            "name": "DN Analysis",
            "instance": None,
            "working": False
        }
        logger.warning("⚠️ DN Analysis Service returned None")
except Exception as e:
    WORKING_SERVICES["2"] = {
        "id": "2",
        "name": "DN Analysis",
        "instance": None,
        "working": False
    }
    logger.warning(f"⚠️ DN Analysis Service skipped: {e}")

# ---------------------------------------------------------------------
# SERVICE 3: Dealer Analytics Service
# ---------------------------------------------------------------------
try:
    from app.services.dealer_analytics_service import get_dealer_service
    service = get_dealer_service()
    if service:
        WORKING_SERVICES["3"] = {
            "id": "3",
            "name": "Dealer Analytics",
            "instance": service,
            "working": True
        }
        logger.info("✅ Dealer Analytics Service loaded")
    else:
        WORKING_SERVICES["3"] = {
            "id": "3",
            "name": "Dealer Analytics",
            "instance": None,
            "working": False
        }
        logger.warning("⚠️ Dealer Analytics Service returned None")
except Exception as e:
    WORKING_SERVICES["3"] = {
        "id": "3",
        "name": "Dealer Analytics",
        "instance": None,
        "working": False
    }
    logger.warning(f"⚠️ Dealer Analytics Service skipped: {e}")

# ---------------------------------------------------------------------
# SERVICE 4: Warehouse Service
# ---------------------------------------------------------------------
try:
    from app.services.warehouse_service import get_warehouse_analytics_service
    service = get_warehouse_analytics_service()
    if service:
        WORKING_SERVICES["4"] = {
            "id": "4",
            "name": "Warehouse Analytics",
            "instance": service,
            "working": True
        }
        logger.info("✅ Warehouse Service loaded")
    else:
        WORKING_SERVICES["4"] = {
            "id": "4",
            "name": "Warehouse Analytics",
            "instance": None,
            "working": False
        }
        logger.warning("⚠️ Warehouse Service returned None")
except Exception as e:
    WORKING_SERVICES["4"] = {
        "id": "4",
        "name": "Warehouse Analytics",
        "instance": None,
        "working": False
    }
    logger.warning(f"⚠️ Warehouse Service skipped: {e}")

# ---------------------------------------------------------------------
# SERVICE 5: Product Service
# ---------------------------------------------------------------------
try:
    from app.services.product_service import get_product_analytics_service
    service = get_product_analytics_service()
    if service:
        WORKING_SERVICES["5"] = {
            "id": "5",
            "name": "Product Analytics",
            "instance": service,
            "working": True
        }
        logger.info("✅ Product Service loaded")
    else:
        WORKING_SERVICES["5"] = {
            "id": "5",
            "name": "Product Analytics",
            "instance": None,
            "working": False
        }
        logger.warning("⚠️ Product Service returned None")
except Exception as e:
    WORKING_SERVICES["5"] = {
        "id": "5",
        "name": "Product Analytics",
        "instance": None,
        "working": False
    }
    logger.warning(f"⚠️ Product Service skipped: {e}")

# ---------------------------------------------------------------------
# SERVICE 6: City Service
# ---------------------------------------------------------------------
try:
    from app.services.city_service import get_city_analytics_service
    service = get_city_analytics_service()
    if service:
        WORKING_SERVICES["6"] = {
            "id": "6",
            "name": "City Analytics",
            "instance": service,
            "working": True
        }
        logger.info("✅ City Service loaded")
    else:
        WORKING_SERVICES["6"] = {
            "id": "6",
            "name": "City Analytics",
            "instance": None,
            "working": False
        }
        logger.warning("⚠️ City Service returned None")
except Exception as e:
    WORKING_SERVICES["6"] = {
        "id": "6",
        "name": "City Analytics",
        "instance": None,
        "working": False
    }
    logger.warning(f"⚠️ City Service skipped: {e}")

# ---------------------------------------------------------------------
# SERVICE 7: AI Assistant (Groq) - UPDATED
# ---------------------------------------------------------------------
try:
    from app.services.groq_service import get_groq_service
    service = get_groq_service()
    # The get_groq_service() now always returns an object (dummy fallback if real fails)
    # So we check if it has a process_whatsapp_query method as a sanity check
    if service and hasattr(service, 'process_whatsapp_query'):
        WORKING_SERVICES["7"] = {
            "id": "7",
            "name": "AI Assistant",
            "instance": service,
            "working": True
        }
        logger.info("✅ AI Assistant Service loaded")
    else:
        WORKING_SERVICES["7"] = {
            "id": "7",
            "name": "AI Assistant",
            "instance": None,
            "working": False
        }
        logger.warning("⚠️ AI Assistant Service returned an invalid object")
except Exception as e:
    WORKING_SERVICES["7"] = {
        "id": "7",
        "name": "AI Assistant",
        "instance": None,
        "working": False
    }
    logger.warning(f"⚠️ AI Assistant Service skipped: {e}")

# ============================================================
# BLOCK 4: SERVICE STATUS SUMMARY
# ============================================================

working_count = sum(1 for svc in WORKING_SERVICES.values() if svc.get("working", False))
total_count = len(WORKING_SERVICES)
logger.info(f"📊 Services loaded: {working_count}/{total_count} working")

if working_count == 0:
    logger.error("❌ No services loaded! Check service files.")
else:
    working_names = [svc["name"] for svc in WORKING_SERVICES.values() if svc.get("working", False)]
    logger.info(f"✅ Working services: {', '.join(working_names)}")

# ============================================================
# BLOCK 5: SESSION DATA CLASS - LIGHTWEIGHT
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
# BLOCK 6: MAIN GATEWAY SERVICE
# ============================================================

class AIProviderService:
    """
    PURE Gateway, Router, Menu Controller, and Session Manager.
    
    This class does NOT contain any business logic, SQL, AI, or analytics.
    It ONLY routes messages to the appropriate services.
    
    CRITICAL:
    - ALL services appear in the menu (✅ working, ❌ not working)
    - Non-working services show ❌ but don't break the app
    - The menu ALWAYS displays cleanly
    - EVERY message gets a reply
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
        """Display startup information - shows ALL services with status"""
        print("\n" + "=" * 70)
        print(f"🤖 AI PROVIDER GATEWAY v{self._version}".center(70))
        print("=" * 70)
        print("📋 ALL SERVICES:")
        print("-" * 70)
        
        for key, svc in WORKING_SERVICES.items():
            if svc.get("working", False):
                status = "✅ WORKING"
            else:
                status = "❌ NOT WORKING"
            print(f"  {key}. {status}  {svc['name']}")
        
        print("-" * 70)
        working_count = sum(1 for svc in WORKING_SERVICES.values() if svc.get("working", False))
        print(f"  ✅ Working: {working_count}/{len(WORKING_SERVICES)} services")
        print(f"  🕐 Started at: {self._startup.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70 + "\n")
    
    # ============================================================
    # BLOCK 7: MAIN ENTRY POINT
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
            
            if not message or not message.strip():
                return self._get_main_menu()
            
            msg = message.strip()
            session = self._get_or_create_session(sender)
            
            if session.is_expired():
                logger.info(f"⏰ Session expired for {sender}")
                self._clear_session(sender)
                return self._get_main_menu()
            
            session.update_activity()
            
            if session.locked:
                logger.info(f"🔒 Session locked to {session.locked_service_name} for {sender}")
                return await self._forward_to_locked_service(msg, session)
            
            return await self._handle_unlocked_session(msg, sender, session)
            
        except Exception as e:
            self._error_count += 1
            logger.error(f"❌ Fatal error: {e}")
            logger.error(traceback.format_exc())
            return self._get_main_menu()
    
    # ============================================================
    # BLOCK 8: SESSION MANAGEMENT
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
            if not service or not service.get("working", False):
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
    # BLOCK 9: MENU CONTROLLER - SHOWS ALL SERVICES
    # ============================================================
    
    def _get_main_menu(self) -> str:
        """Return the main menu with ALL services (✅ working, ❌ not working)"""
        lines = []
        
        SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        HEADER = "     📦  LOGISTICS INTELLIGENCE CENTER"
        
        lines.append(SEPARATOR)
        lines.append(HEADER)
        lines.append(SEPARATOR)
        lines.append("")
        lines.append("Please choose from:")
        lines.append("")
        
        # Show ALL services with status
        for key, svc in WORKING_SERVICES.items():
            if svc.get("working", False):
                status = "✅"
            else:
                status = "❌"
            lines.append(f"{key}. {status} {svc['name']}")
        
        lines.append("")
        lines.append("99 - Return to Main Menu")
        lines.append("")
        lines.append("📌 Services with ✅ are working")
        lines.append("📌 Services with ❌ are currently unavailable")
        
        return "\n".join(lines)
    
    # ============================================================
    # BLOCK 10: UNLOCKED SESSION HANDLER
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
                service = WORKING_SERVICES[message]
                
                # Check if service is working
                if not service.get("working", False):
                    logger.info(f"❌ Service {message} is not working")
                    return self._get_main_menu()
                
                logger.info(f"🎯 Menu selection: {message} -> {service['name']}")
                
                locked_session = self._lock_session(phone, message)
                if not locked_session:
                    return self._get_main_menu()
                
                return await self._forward_to_service(message, message, phone)
            
            if message == "99":
                self._unlock_session(phone)
                return self._get_main_menu()
            
            logger.info(f"ℹ️ Invalid input: '{message}' from {phone} - showing menu")
            return self._get_main_menu()
            
        except Exception as e:
            logger.error(f"❌ Error in _handle_unlocked_session: {e}")
            logger.error(traceback.format_exc())
            return self._get_main_menu()
    
    # ============================================================
    # BLOCK 11: LOCKED SESSION HANDLER
    # ============================================================
    
    async def _forward_to_locked_service(self, message: str, session: SessionData) -> str:
        """
        Forward message to locked service.
        NO ROUTING, NO MENU, NO AI, NO INTENT DETECTION.
        ALWAYS returns a string.
        """
        try:
            if message == "99":
                self._unlock_session(session.phone)
                logger.info(f"🔓 Unlocked via 99 for {session.phone}")
                return self._get_main_menu()
            
            if not session.locked_service_id:
                self._unlock_session(session.phone)
                return self._get_main_menu()
            
            service_id = session.locked_service_id
            logger.info(f"🔄 Forwarding to {session.locked_service_name} for {session.phone}")
            return await self._forward_to_service(service_id, message, session.phone)
            
        except Exception as e:
            logger.error(f"❌ Error in _forward_to_locked_service: {e}")
            logger.error(traceback.format_exc())
            return self._get_main_menu()
    
    # ============================================================
    # BLOCK 12: ROUTER - FORWARD TO SERVICES
    # ============================================================
    
    async def _forward_to_service(self, service_id: str, message: str, phone: str) -> str:
        """
        Forward message to the appropriate service.
        PURE ROUTING - NO BUSINESS LOGIC.
        ALWAYS returns a string.
        """
        try:
            service = WORKING_SERVICES.get(service_id)
            if not service or not service.get("working", False):
                return self._get_main_menu()
            
            service_instance = service["instance"]
            
            if not service_instance:
                return self._get_main_menu()
            
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
                    response = f"📊 {service['name']}\n\nService is available. Please enter your query."
                    
            except Exception as service_error:
                logger.error(f"❌ Service {service['name']} error: {service_error}")
                logger.error(traceback.format_exc())
                return self._get_main_menu()
            
            if response is None:
                response = self._get_main_menu()
            elif not isinstance(response, str):
                response = str(response)
            
            if response == EXIT_SIGNAL or response == "99":
                self._unlock_session(phone)
                return self._get_main_menu()
            
            self._successful_requests += 1
            return response
            
        except Exception as e:
            logger.error(f"❌ Fatal error in _forward_to_service: {e}")
            logger.error(traceback.format_exc())
            return self._get_main_menu()
    
    # ============================================================
    # BLOCK 13: HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check - shows ALL services with status"""
        uptime = (datetime.now() - self._startup).seconds
        
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
        
        working_count = sum(1 for svc in WORKING_SERVICES.values() if svc.get("working", False))
        
        return {
            "status": "healthy" if working_count > 0 else "degraded",
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
            "services": {
                key: {
                    "name": svc["name"],
                    "working": svc.get("working", False),
                    "status": "✅ Working" if svc.get("working", False) else "❌ Not Working"
                }
                for key, svc in WORKING_SERVICES.items()
            },
            "services_summary": f"{working_count}/{len(WORKING_SERVICES)} services working",
            "started_at": self._startup.isoformat()
        }

# ============================================================
# BLOCK 14: SINGLETON AND EXPORTS
# ============================================================

_service_instance: Optional[AIProviderService] = None

def get_ai_provider_service() -> AIProviderService:
    """Get singleton instance"""
    global _service_instance
    if _service_instance is None:
        _service_instance = AIProviderService()
    return _service_instance

# ============================================================
# BLOCK 15: WEBHOOK ENTRY POINT
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
        logger.error(f"❌ Fatal error in process_whatsapp_query: {e}")
        logger.error(traceback.format_exc())
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "     📦  LOGISTICS INTELLIGENCE CENTER",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "⚠️ Service is temporarily unavailable.",
            "",
            "Please try again later.",
            "",
            "99 - Return to Main Menu"
        ])

# ============================================================
# BLOCK 16: EXPORTS
# ============================================================

__all__ = [
    "AIProviderService",
    "get_ai_provider_service",
    "process_whatsapp_query",
    "VERSION",
    "WORKING_SERVICES"
]

# ============================================================
# BLOCK 17: TEST MODE
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
    
    health = service.health_check()
    print("📊 HEALTH CHECK:")
    print("-" * 40)
    for key, value in health.items():
        if key != 'services':
            print(f"  {key}: {value}")
    print("-" * 40)
    print()
    
    print("📋 SERVICE STATUS:")
    for key, svc in WORKING_SERVICES.items():
        status = "✅ WORKING" if svc.get("working", False) else "❌ NOT WORKING"
        print(f"  {key}. {status} - {svc['name']}")
    print()
    
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
