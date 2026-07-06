#!/usr/bin/env python3
# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 52.0 - PRODUCTION GATEWAY WITH ERROR HANDLING
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
- Receives WhatsApp messages from webhook.py
- Manages sessions
- Shows Main Menu
- Locks selected modules
- Forwards messages to services
- Unlocks on 99
- Handles ALL errors gracefully
- Ensures EVERY message gets a reply

Services (Standard Interface):
    Each service exposes: async def handle_message(message: str, sender: str) -> str

Services:
    1 → National KPI Service
    2 → DN Analysis Service
    3 → Dealer Analytics Service
    4 → Warehouse Service
    5 → Product Service
    6 → City Service
    7 → AI Assistant (Groq)
================================================================================
"""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Optional, Dict, Any
from datetime import datetime

# ============================================================
# LOGGING SETUP
# ============================================================

logger = logging.getLogger(__name__)

# ============================================================
# CONSTANTS
# ============================================================

VERSION = "52.0"
EXIT_SIGNAL = "__EXIT__"
SESSION_TIMEOUT_SECONDS = 1800  # 30 minutes

# ============================================================
# SERVICE REGISTRY - STANDARD INTERFACE
# ============================================================

# Each service must expose: async def handle_message(message: str, sender: str) -> str
SERVICE_REGISTRY = {}

# 1. National KPI Service
try:
    from app.services.national_kpi_service import get_kpi_service
    SERVICE_REGISTRY["1"] = {
        "name": "National KPI",
        "available": True,
        "getter": get_kpi_service,
        "method": "handle_message"
    }
    logger.info("✅ National KPI Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ National KPI Service not available: {e}")
    SERVICE_REGISTRY["1"] = {
        "name": "National KPI",
        "available": False,
        "getter": None,
        "method": None,
        "error": str(e)
    }

# 2. DN Analysis Service
try:
    from app.services.dn_analysis import get_dn_analysis_service
    SERVICE_REGISTRY["2"] = {
        "name": "DN Analysis",
        "available": True,
        "getter": get_dn_analysis_service,
        "method": "handle_message"
    }
    logger.info("✅ DN Analysis Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ DN Analysis Service not available: {e}")
    SERVICE_REGISTRY["2"] = {
        "name": "DN Analysis",
        "available": False,
        "getter": None,
        "method": None,
        "error": str(e)
    }

# 3. Dealer Analytics Service
try:
    from app.services.dealer_analytics_service import get_dealer_service
    SERVICE_REGISTRY["3"] = {
        "name": "Dealer Analytics",
        "available": True,
        "getter": get_dealer_service,
        "method": "handle_message"
    }
    logger.info("✅ Dealer Analytics Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Dealer Analytics Service not available: {e}")
    SERVICE_REGISTRY["3"] = {
        "name": "Dealer Analytics",
        "available": False,
        "getter": None,
        "method": None,
        "error": str(e)
    }

# 4. Warehouse Service
try:
    from app.services.warehouse_service import get_warehouse_analytics_service
    SERVICE_REGISTRY["4"] = {
        "name": "Warehouse Analytics",
        "available": True,
        "getter": get_warehouse_analytics_service,
        "method": "handle_message"
    }
    logger.info("✅ Warehouse Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Warehouse Service not available: {e}")
    SERVICE_REGISTRY["4"] = {
        "name": "Warehouse Analytics",
        "available": False,
        "getter": None,
        "method": None,
        "error": str(e)
    }

# 5. Product Service
try:
    from app.services.product_service import get_product_analytics_service
    SERVICE_REGISTRY["5"] = {
        "name": "Product Analytics",
        "available": True,
        "getter": get_product_analytics_service,
        "method": "handle_message"
    }
    logger.info("✅ Product Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Product Service not available: {e}")
    SERVICE_REGISTRY["5"] = {
        "name": "Product Analytics",
        "available": False,
        "getter": None,
        "method": None,
        "error": str(e)
    }

# 6. City Service
try:
    from app.services.city_service import get_city_analytics_service
    SERVICE_REGISTRY["6"] = {
        "name": "City Analytics",
        "available": True,
        "getter": get_city_analytics_service,
        "method": "handle_message"
    }
    logger.info("✅ City Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ City Service not available: {e}")
    SERVICE_REGISTRY["6"] = {
        "name": "City Analytics",
        "available": False,
        "getter": None,
        "method": None,
        "error": str(e)
    }

# 7. Groq AI Service (Fallback)
try:
    from app.services.groq_service import get_groq_service
    SERVICE_REGISTRY["7"] = {
        "name": "AI Assistant",
        "available": True,
        "getter": get_groq_service,
        "method": "handle_message"
    }
    logger.info("✅ AI Assistant Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ AI Assistant Service not available: {e}")
    SERVICE_REGISTRY["7"] = {
        "name": "AI Assistant",
        "available": False,
        "getter": None,
        "method": None,
        "error": str(e)
    }

# ============================================================
# SESSION DATA CLASS - LIGHTWEIGHT
# ============================================================

class SessionData:
    """Lightweight user session data - pure session state only"""
    
    def __init__(self, phone: str):
        self.phone: str = phone
        self.locked: bool = False
        self.locked_service: Optional[str] = None
        self.menu_id: Optional[int] = None
        self.created_at: datetime = datetime.now()
        self.last_activity: datetime = datetime.now()
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.now()
    
    def is_expired(self) -> bool:
        """Check if session has expired (30 minutes timeout)"""
        elapsed = (datetime.now() - self.last_activity).total_seconds()
        return elapsed > SESSION_TIMEOUT_SECONDS
    
    def lock(self, service_name: str, menu_id: int):
        """Lock session to a specific service"""
        self.locked = True
        self.locked_service = service_name
        self.menu_id = menu_id
        self.update_activity()
    
    def unlock(self):
        """Unlock session"""
        self.locked = False
        self.locked_service = None
        self.menu_id = None
        self.update_activity()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dict for logging"""
        return {
            "phone": self.phone,
            "locked": self.locked,
            "locked_service": self.locked_service,
            "menu_id": self.menu_id,
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
    
    Ensures EVERY message gets a reply.
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
        """Display startup information with service status"""
        print("\n" + "=" * 70)
        print(f"🤖 AI PROVIDER GATEWAY v{self._version} - PURE ROUTER".center(70))
        print("=" * 70)
        print("📋 SERVICES STATUS:")
        print("-" * 70)
        
        working_count = 0
        total_count = 0
        
        for key, svc in SERVICE_REGISTRY.items():
            total_count += 1
            if svc["available"]:
                working_count += 1
                status = "✅ WORKING"
            else:
                status = "❌ NOT WORKING"
                error = svc.get("error", "Unknown error")
            
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
                logger.info(f"🔒 Session locked to {session.locked_service} for {sender}")
                response = await self._forward_to_locked_service(msg, session)
                return response
            
            # Session is NOT locked - handle menu or routing
            return await self._handle_unlocked_session(msg, sender, session)
            
        except Exception as e:
            # CRITICAL: ALWAYS return a response, never let an exception escape
            self._error_count += 1
            logger.error(f"❌ Fatal error in process_whatsapp_query: {e}")
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
            service = SERVICE_REGISTRY.get(menu_id)
            if not service or not service["available"]:
                return None
            
            session.lock(service["name"], int(menu_id))
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
    
    def _get_session_info(self, phone: str) -> Optional[Dict[str, Any]]:
        """Get session info for logging"""
        with self._lock:
            if phone in self._sessions:
                return self._sessions[phone].to_dict()
            return None
    
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
        for key, svc in SERVICE_REGISTRY.items():
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
        
        for key, svc in SERVICE_REGISTRY.items():
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
            for key, svc in SERVICE_REGISTRY.items()
        ] + [
            "",
            "99 - Return to Main Menu"
        ])
    
    def _get_error_message(self) -> str:
        """Return friendly error message"""
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
        return message in list(SERVICE_REGISTRY.keys()) + ["99"]
    
    # ============================================================
    # UNLOCKED SESSION HANDLER
    # ============================================================
    
    async def _handle_unlocked_session(self, message: str, phone: str, session: SessionData) -> str:
        """
        Handle messages when session is NOT locked.
        Shows menu or routes selection.
        ALWAYS returns a string.
        """
        try:
            # Check if it's a valid menu selection
            if self._is_menu_selection(message):
                # Check for 99 - unlock/return to menu
                if message == "99":
                    self._unlock_session(phone)
                    return self._get_main_menu()
                
                # Valid selection 1-7 - lock and route
                service = SERVICE_REGISTRY.get(message)
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
            
        except Exception as e:
            logger.error(f"❌ Error in _handle_unlocked_session: {e}")
            logger.error(traceback.format_exc())
            return self._get_error_message()
    
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
            if not session.menu_id:
                self._unlock_session(session.phone)
                return self._get_main_menu()
            
            menu_id = str(session.menu_id)
            logger.info(f"🔄 Forwarding to {session.locked_service} for {session.phone}")
            return await self._forward_to_service(menu_id, message, session.phone)
            
        except Exception as e:
            logger.error(f"❌ Error in _forward_to_locked_service: {e}")
            logger.error(traceback.format_exc())
            return self._get_error_message()
    
    # ============================================================
    # ROUTER - FORWARD TO SERVICES
    # ============================================================
    
    async def _forward_to_service(self, service_key: str, message: str, phone: str) -> str:
        """
        Forward message to the appropriate service.
        PURE ROUTING - NO BUSINESS LOGIC.
        ALWAYS returns a string.
        """
        try:
            service = SERVICE_REGISTRY.get(service_key)
            if not service or not service["available"]:
                return self._get_service_unavailable_message(
                    service["name"] if service else "Service"
                )
            
            # Get service instance (fresh each time)
            service_instance = service["getter"]()
            
            if not service_instance:
                return self._get_service_unavailable_message(service["name"])
            
            # Call the standard handle_message method
            response = None
            
            try:
                # Try async handle_message first
                if hasattr(service_instance, 'handle_message') and callable(service_instance.handle_message):
                    response = await service_instance.handle_message(message, phone)
                elif hasattr(service_instance, 'process_whatsapp_query') and callable(service_instance.process_whatsapp_query):
                    response = service_instance.process_whatsapp_query(message, phone)
                else:
                    # Fallback: try get_main_menu if available
                    if hasattr(service_instance, 'get_main_menu') and callable(service_instance.get_main_menu):
                        response = service_instance.get_main_menu()
                    else:
                        response = f"📊 {service['name']} Service\n\nService is available but not properly configured."
                
            except Exception as service_error:
                # Catch ANY exception from the service
                logger.error(f"❌ Service {service['name']} error: {service_error}")
                logger.error(traceback.format_exc())
                return self._get_error_message()
            
            # Ensure we have a string response
            if response is None:
                response = f"📊 {service['name']} Service\n\nNo response received. Please try again."
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
        working_services = sum(1 for svc in SERVICE_REGISTRY.values() if svc["available"])
        total_services = len(SERVICE_REGISTRY)
        
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
            "error_count": self._error_count,
            "success_rate": round((self._successful_requests / max(self._total_requests, 1)) * 100, 1),
            "services": {
                key: {
                    "name": svc["name"],
                    "available": svc["available"],
                    "status": "✅ Working" if svc["available"] else "❌ Not Working"
                }
                for key, svc in SERVICE_REGISTRY.items()
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
        Response string - ALWAYS returns a string, never None
    """
    try:
        service = get_ai_provider_service()
        return await service.process_whatsapp_query(message, sender)
    except Exception as e:
        # CRITICAL: ALWAYS return a response, never let an exception escape to webhook
        logger.error(f"❌ Fatal error in process_whatsapp_query: {e}")
        logger.error(traceback.format_exc())
        return "\n".join([
            "⚠️ Service is temporarily unavailable.",
            "",
            "Please try again later.",
            "",
            "Reply 99 to return to the main menu."
        ])

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "AIProviderService",
    "get_ai_provider_service",
    "process_whatsapp_query",
    "VERSION",
    "SERVICE_REGISTRY"
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
    for key, svc in SERVICE_REGISTRY.items():
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
                traceback.print_exc()
    
    asyncio.run(test_loop())
