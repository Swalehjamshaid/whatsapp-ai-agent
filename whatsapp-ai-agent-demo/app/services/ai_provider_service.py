# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 41.0 - ENTERPRISE SESSION ROUTER WITH OUT OF BOX HANDLING
# ============================================================

"""
File: app/services/ai_provider_service.py
Version: 41.0 - ENTERPRISE SESSION ROUTER

================================================================================
PURPOSE
================================================================================

This file is a 100% Enterprise Session Router.

Its ONLY responsibility is:
1. Receive WhatsApp message
2. Detect which service should handle the conversation
3. Route the conversation ONCE
4. Suspend itself
5. Wait until the selected service releases the session
6. Resume only after the service exits

================================================================================
OUT OF BOX HANDLING
================================================================================

When a user asks something that doesn't match any menu option or service:
- ✅ Shows: "Please select a valid option from the menu."
- ✅ Shows the main menu
- ✅ Does NOT try to interpret or answer the question

================================================================================
FORBIDDEN
================================================================================

This file is NOT ALLOWED to perform ANY business logic:

- ❌ Search DN, Dealer, Warehouse, Product, City
- ❌ Build Dashboard
- ❌ Execute SQL
- ❌ Query PostgreSQL
- ❌ Calculate KPI, Revenue, Units, Pending
- ❌ Generate Executive Dashboard
- ❌ Call Analytics Functions
- ❌ Detect Follow-up Questions
- ❌ Detect Dashboard Type
- ❌ Intent Detection (when locked)
- ❌ Entity Detection (when locked)
- ❌ AI Engine (when locked)
- ❌ Answer questions directly

================================================================================
GOLDEN RULE
================================================================================

The router routes exactly ONCE.
After that, it becomes completely passive.
The selected domain service owns the conversation.
The router must NEVER inspect, reinterpret, or reroute messages while locked.

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union, Set, Tuple, Callable
from functools import lru_cache, wraps

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: CONFIGURATION
# ============================================================

SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
CACHE_TTL = int(os.getenv("ROUTER_CACHE_TTL", "300"))

# ============================================================
# BLOCK 2: ENUMS
# ============================================================

class ServiceType(Enum):
    """Available domain services"""
    DN = "dn"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    CITY = "city"
    PRODUCT = "product"
    NATIONAL = "national"
    INVENTORY = "inventory"
    SALES_OFFICE = "sales_office"
    TRANSPORT = "transport"
    FORECAST = "forecast"
    REPORTS = "reports"
    MANAGEMENT = "management"
    AI = "ai"
    MAIN = "main"

class SessionState(Enum):
    """Session states"""
    IDLE = "idle"
    LOCKED = "locked"
    UNLOCKING = "unlocking"

# ============================================================
# BLOCK 3: DATACLASSES
# ============================================================

@dataclass
class SessionOwner:
    """Session ownership information"""
    service_type: ServiceType
    service_instance: Any
    started_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    locked: bool = True
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.now()
    
    def is_expired(self, ttl: int = SESSION_TTL) -> bool:
        """Check if session is expired"""
        return (datetime.now() - self.last_activity).total_seconds() > ttl

# ============================================================
# BLOCK 4: LAZY LOADER - Only for initial routing
# ============================================================

class ServiceLoader:
    """Lazy load services only when needed for initial routing"""
    
    _instances = {}
    _lock = threading.Lock()
    
    @classmethod
    def get_dn_service(cls):
        """Lazy load DN service"""
        if "dn" not in cls._instances:
            with cls._lock:
                if "dn" not in cls._instances:
                    try:
                        from app.services.dn_analysis import DNAnalysisService
                        cls._instances["dn"] = DNAnalysisService()
                        logger.info("✅ DN service loaded")
                    except Exception as e:
                        logger.error(f"❌ DN service load failed: {e}")
                        cls._instances["dn"] = None
        return cls._instances["dn"]
    
    @classmethod
    def get_dealer_service(cls):
        """Lazy load Dealer service"""
        if "dealer" not in cls._instances:
            with cls._lock:
                if "dealer" not in cls._instances:
                    try:
                        from app.services.dealer_analytics_service import DealerAnalyticsService
                        cls._instances["dealer"] = DealerAnalyticsService()
                        logger.info("✅ Dealer service loaded")
                    except Exception as e:
                        logger.error(f"❌ Dealer service load failed: {e}")
                        cls._instances["dealer"] = None
        return cls._instances["dealer"]
    
    @classmethod
    def get_city_service(cls):
        """Lazy load City service"""
        if "city" not in cls._instances:
            with cls._lock:
                if "city" not in cls._instances:
                    try:
                        from app.services.city_service import CityAnalyticsService
                        cls._instances["city"] = CityAnalyticsService()
                        logger.info("✅ City service loaded")
                    except Exception as e:
                        logger.error(f"❌ City service load failed: {e}")
                        cls._instances["city"] = None
        return cls._instances["city"]
    
    @classmethod
    def get_warehouse_service(cls):
        """Lazy load Warehouse service"""
        if "warehouse" not in cls._instances:
            with cls._lock:
                if "warehouse" not in cls._instances:
                    try:
                        from app.services.warehouse_service import WarehouseAnalyticsService
                        cls._instances["warehouse"] = WarehouseAnalyticsService()
                        logger.info("✅ Warehouse service loaded")
                    except Exception as e:
                        logger.error(f"❌ Warehouse service load failed: {e}")
                        cls._instances["warehouse"] = None
        return cls._instances["warehouse"]
    
    @classmethod
    def get_product_service(cls):
        """Lazy load Product service"""
        if "product" not in cls._instances:
            with cls._lock:
                if "product" not in cls._instances:
                    try:
                        from app.services.product_service import ProductAnalyticsService
                        cls._instances["product"] = ProductAnalyticsService()
                        logger.info("✅ Product service loaded")
                    except Exception as e:
                        logger.error(f"❌ Product service load failed: {e}")
                        cls._instances["product"] = None
        return cls._instances["product"]
    
    @classmethod
    def get_national_service(cls):
        """Lazy load National KPI service"""
        if "national" not in cls._instances:
            with cls._lock:
                if "national" not in cls._instances:
                    try:
                        from app.services.national_kpi_service import NationalKPIService
                        cls._instances["national"] = NationalKPIService()
                        logger.info("✅ National KPI service loaded")
                    except Exception as e:
                        logger.error(f"❌ National KPI service load failed: {e}")
                        cls._instances["national"] = None
        return cls._instances["national"]

# ============================================================
# BLOCK 5: MAIN SESSION ROUTER
# ============================================================

class AIProviderService:
    """
    100% Enterprise Session Router
    
    ONLY responsibility: Route messages to the correct service.
    NO business logic.
    NO SQL.
    NO calculations.
    NO analytics.
    NO answering questions directly.
    
    Routes exactly ONCE per session.
    Then becomes completely passive.
    """
    
    _instance: Optional["AIProviderService"] = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if AIProviderService._initialized:
            return
        
        self._initialized = True
        
        # Session ownership tracking
        self._sessions: Dict[str, SessionOwner] = {}
        self._session_lock = threading.RLock()
        
        # Service loader
        self._loader = ServiceLoader()
        
        # Cache for responses
        self._cache: TTLCache = TTLCache(maxsize=1000, ttl=CACHE_TTL)
        self._cache_lock = threading.RLock()
        
        logger.info("=" * 70)
        logger.info("🚀 ENTERPRISE SESSION ROUTER v41.0 initialized")
        logger.info("   📦 ONLY responsible for ROUTING")
        logger.info("   🔒 Routes ONCE per session")
        logger.info("   🔑 Suspends itself after routing")
        logger.info("   📡 Forwards messages to active service")
        logger.info("   🚫 NO business logic")
        logger.info("   🚫 NO SQL queries")
        logger.info("   🚫 NO analytics")
        logger.info("   🚫 NO answering questions directly")
        logger.info("   📋 Shows menu for invalid/out-of-box queries")
        logger.info("=" * 70)
    
    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================
    
    def _get_owner(self, sender: str) -> Optional[SessionOwner]:
        """Get session owner for sender"""
        with self._session_lock:
            return self._sessions.get(sender)
    
    def _set_owner(self, sender: str, owner: SessionOwner):
        """Set session owner for sender"""
        with self._session_lock:
            self._sessions[sender] = owner
            logger.info(f"🔒 Session LOCKED for {sender} → {owner.service_type.value}")
    
    def _release_owner(self, sender: str):
        """Release session owner"""
        with self._session_lock:
            if sender in self._sessions:
                owner = self._sessions[sender]
                logger.info(f"🔓 Session UNLOCKED for {sender} → {owner.service_type.value}")
                del self._sessions[sender]
    
    def _is_locked(self, sender: str) -> bool:
        """Check if session is locked"""
        with self._session_lock:
            return sender in self._sessions
    
    def _cleanup_expired(self):
        """Clean up expired sessions"""
        with self._session_lock:
            expired = []
            for sender, owner in self._sessions.items():
                if owner.is_expired():
                    expired.append(sender)
            for sender in expired:
                logger.info(f"🧹 Expired session cleaned: {sender}")
                del self._sessions[sender]
    
    # ============================================================
    # ROUTING - ONLY ROUTING LOGIC HERE
    # ============================================================
    
    def _route_to_service(self, message: str, sender: str) -> Optional[SessionOwner]:
        """
        Route message to appropriate service.
        Called ONLY when session is IDLE.
        Routes exactly ONCE.
        """
        message_clean = message.strip()
        
        # ============================================================
        # GLOBAL COMMANDS: ONLY "0" and "99"
        # ============================================================
        if message_clean == "99":
            return None  # Return to main menu
        if message_clean == "0":
            return None  # Return to main menu
        
        # ============================================================
        # MENU COMMAND: Show main menu
        # ============================================================
        if message_clean.lower() in ["menu", "help", "options", "show menu", "main menu"]:
            return None  # Show main menu
        
        # ============================================================
        # MENU NUMBER: Route to service
        # ============================================================
        if message_clean in ["1", "2", "3", "4", "5", "6", "7", "8", "9"]:
            service_map = {
                "1": ServiceType.DN,
                "2": ServiceType.DEALER,
                "3": ServiceType.CITY,
                "4": ServiceType.WAREHOUSE,
                "5": ServiceType.PRODUCT,
                "6": ServiceType.NATIONAL,
                "7": ServiceType.DN,
                "8": ServiceType.DN,
                "9": ServiceType.AI,
            }
            service_type = service_map.get(message_clean)
            if service_type:
                service_instance = self._get_service_instance(service_type)
                if service_instance:
                    return SessionOwner(
                        service_type=service_type,
                        service_instance=service_instance
                    )
                else:
                    logger.warning(f"⚠️ Service {service_type.value} not available")
                    return None
        
        # ============================================================
        # DN NUMBER: Auto-detect 8-12 digit numbers
        # ============================================================
        if re.match(r'^\d{8,12}$', message_clean):
            service_instance = self._loader.get_dn_service()
            if service_instance:
                return SessionOwner(
                    service_type=ServiceType.DN,
                    service_instance=service_instance
                )
        
        # ============================================================
        # OUT OF BOX: No route found - Show menu with message
        # ============================================================
        logger.info(f"📋 Out of box query: '{message_clean}' from {sender} - Showing menu")
        return None  # Will trigger "Please select from menu" response
    
    def _get_service_instance(self, service_type: ServiceType) -> Any:
        """Get service instance by type"""
        loaders = {
            ServiceType.DN: self._loader.get_dn_service,
            ServiceType.DEALER: self._loader.get_dealer_service,
            ServiceType.CITY: self._loader.get_city_service,
            ServiceType.WAREHOUSE: self._loader.get_warehouse_service,
            ServiceType.PRODUCT: self._loader.get_product_service,
            ServiceType.NATIONAL: self._loader.get_national_service,
        }
        loader = loaders.get(service_type)
        if loader:
            return loader()
        return None
    
    # ============================================================
    # MAIN PROCESSING - ROUTER ONLY
    # ============================================================
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """
        MAIN ENTRY POINT - ROUTER ONLY
        
        Flow:
        1. Check if session is LOCKED
           → YES: Forward to active service (NO ROUTING)
           → NO: Route to service
        2. If routed, LOCK session
        3. Forward message to active service
        4. Check if service returned "exit"
        5. If exit, UNLOCK session
        6. Return response
        7. If NO route found, show menu with "Please select from menu"
        """
        sender = sender or sender_id or "default"
        
        if not message or not message.strip():
            return self._get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📨 Router received: '{message_clean}' from {sender}")
        
        # Cleanup expired sessions
        self._cleanup_expired()
        
        # ============================================================
        # CHECK: Is session LOCKED?
        # ============================================================
        owner = self._get_owner(sender)
        
        if owner:
            # ============================================================
            # SESSION LOCKED: FORWARD ONLY - NO ROUTING!
            # ============================================================
            logger.info(f"🔒 Session LOCKED → Forwarding to {owner.service_type.value}")
            
            # Update activity
            owner.update_activity()
            
            # Forward to service
            service = owner.service_instance
            if service and hasattr(service, "process_whatsapp_query"):
                try:
                    result = service.process_whatsapp_query(message_clean, sender)
                    
                    # Check if service wants to exit
                    if result == "99" or (isinstance(result, str) and "99" in result and "exit" in result.lower()):
                        logger.info(f"🔓 Service {owner.service_type.value} released session (99)")
                        self._release_owner(sender)
                        return self._get_main_menu()
                    
                    return result
                except Exception as e:
                    logger.error(f"❌ Service {owner.service_type.value} error: {e}")
                    # On error, release session
                    self._release_owner(sender)
                    return f"⚠️ Service error: {str(e)[:200]}\n\nPlease try again or type 'menu' for options."
            else:
                # Service not available, release session
                logger.warning(f"⚠️ Service {owner.service_type.value} not available, releasing session")
                self._release_owner(sender)
                return self._get_main_menu()
        
        # ============================================================
        # SESSION IDLE: ROUTE ONCE
        # ============================================================
        logger.info(f"🔄 Session IDLE → Routing '{message_clean}' from {sender}")
        
        # Route to service
        new_owner = self._route_to_service(message_clean, sender)
        
        if new_owner:
            # LOCK session
            self._set_owner(sender, new_owner)
            
            # Forward to service
            service = new_owner.service_instance
            if service and hasattr(service, "process_whatsapp_query"):
                try:
                    result = service.process_whatsapp_query(message_clean, sender)
                    
                    # Check if service immediately exits
                    if result == "99" or (isinstance(result, str) and "99" in result and "exit" in result.lower()):
                        logger.info(f"🔓 Service {new_owner.service_type.value} released session immediately")
                        self._release_owner(sender)
                        return self._get_main_menu()
                    
                    return result
                except Exception as e:
                    logger.error(f"❌ Service {new_owner.service_type.value} error: {e}")
                    self._release_owner(sender)
                    return f"⚠️ Service error: {str(e)[:200]}\n\nPlease try again or type 'menu' for options."
            else:
                self._release_owner(sender)
                return f"⚠️ Service {new_owner.service_type.value} is not available."
        
        # ============================================================
        # NO ROUTE FOUND: OUT OF BOX - Show menu with message
        # ============================================================
        return self._get_out_of_box_response()
    
    # ============================================================
    # OUT OF BOX RESPONSE
    # ============================================================
    
    def _get_out_of_box_response(self) -> str:
        """
        Response when user asks something out of the box.
        Shows the menu with a clear message.
        """
        return "\n".join([
            "❌ *Please select a valid option from the menu.*",
            "",
            "I can only route you to the available services.",
            "Please choose from the menu below:",
            "",
            self._get_main_menu()
        ])
    
    # ============================================================
    # MAIN MENU - ONLY THE ROUTER SHOWS THIS
    # ============================================================
    
    def _get_main_menu(self) -> str:
        """Main menu - ONLY the router shows this"""
        return (
            "📋 *AI LOGISTICS MENU*\n\n"
            "0. Main Menu\n"
            "1. DN Delivery\n"
            "2. Dealer Analytics\n"
            "3. City Analytics\n"
            "4. Warehouse Analytics\n"
            "5. Product Analytics\n"
            "6. National KPI\n"
            "7. Pending DN\n"
            "8. Top Performers\n"
            "9. AI Query\n\n"
            "📌 *Commands:*\n"
            "• Type a number from 0-9 to enter a service\n"
            "• Type '99' to return to main menu\n"
            "• Type 'menu' or 'help' at any time\n\n"
            "Reply with a number from 0 to 9."
        )
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for router"""
        with self._session_lock:
            active_sessions = len(self._sessions)
            session_details = {
                sender: {
                    "service": owner.service_type.value,
                    "locked": owner.locked,
                    "started_at": owner.started_at.isoformat(),
                    "last_activity": owner.last_activity.isoformat(),
                }
                for sender, owner in self._sessions.items()
            }
        
        return {
            "service": "ai_provider_service",
            "version": "41.0",
            "type": "enterprise_session_router",
            "status": "healthy",
            "active_sessions": active_sessions,
            "session_details": session_details,
            "cache_size": len(self._cache),
            "features": {
                "out_of_box_handling": True,
                "session_locking": True,
                "route_once": True,
                "forward_only_when_locked": True,
            }
        }


# ============================================================
# BLOCK 6: SINGLETON
# ============================================================

_ai_service: Optional[AIProviderService] = None
_service_lock = threading.Lock()

def get_ai_provider_service() -> AIProviderService:
    """Get singleton instance"""
    global _ai_service
    if _ai_service is None:
        with _service_lock:
            if _ai_service is None:
                _ai_service = AIProviderService()
    return _ai_service

async def process_whatsapp_query(
    message: str,
    sender: Optional[str] = None,
    sender_id: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Process WhatsApp query through the router"""
    try:
        return await get_ai_provider_service().process_whatsapp_query(
            message=message,
            sender=sender,
            sender_id=sender_id,
            **kwargs,
        )
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return "⚠️ Service is temporarily unavailable. Please try again."


# ============================================================
# BLOCK 7: EXPORTS
# ============================================================

__all__ = [
    "AIProviderService",
    "ServiceType",
    "SessionOwner",
    "get_ai_provider_service",
    "process_whatsapp_query",
]
