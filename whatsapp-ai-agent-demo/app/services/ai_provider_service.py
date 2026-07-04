# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 50.0 - ENTERPRISE GATEWAY (FIXED SESSION LOCKING)
# ============================================================

"""
File: app/services/ai_provider_service.py
Version: 50.0 - ENTERPRISE GATEWAY (FIXED SESSION LOCKING)

================================================================================
PURPOSE
================================================================================

This is the SOLE GATEWAY for all WhatsApp interactions.

Its ONLY responsibilities are:
1. Detect if session is locked to a module
2. If locked → Forward EVERY message to that module (NO ROUTING)
3. If unlocked → Show Main Dashboard or Route to selected module
4. ONLY "__EXIT__" or "99" unlocks the session and returns to Main Dashboard

================================================================================
INTEGRATED SERVICES (7 Files)
================================================================================

Menu ID | Dashboard Name          | Service File           | Loader Function
--------|-------------------------|------------------------|-------------------------------
1       | National Dashboard      | national_kpi_service.py| get_national_kpi_service()
2       | DN Dashboard            | dn_analysis.py (v31.0) | get_dn_analysis_service()
3       | Dealer Dashboard        | dealer_service.py      | get_dealer_service()
4       | Warehouse Dashboard     | warehouse_service.py   | get_warehouse_analytics_service()
5       | Product Dashboard       | product_service.py     | get_product_analytics_service()
6       | City Dashboard          | city_service.py        | get_city_analytics_service()
7       | AI Chat                 | groq_service.py        | get_groq_service()

================================================================================
SESSION LOCKING
================================================================================

Once a user enters a module:
- Session is LOCKED
- ALL messages go directly to that module
- NO routing occurs
- Only "__EXIT__" or "99" unlocks

================================================================================
FIXES IN V50.0
================================================================================

1. Fixed session locking - session is now properly locked when entering a module
2. Added debug logging for session state
3. Fixed async/sync handling
4. Added better error handling
5. Fixed module forwarding

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union, Callable

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: CONFIGURATION
# ============================================================

SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "1800"))  # 30 minutes
EXIT_SIGNAL = "__EXIT__"

# ============================================================
# BLOCK 2: ENUMS
# ============================================================

class ModuleType(Enum):
    """Available domain modules"""
    NATIONAL = "national"
    DN = "dn"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    PRODUCT = "product"
    CITY = "city"
    AI = "ai"

# ============================================================
# BLOCK 3: DATA CLASSES
# ============================================================

@dataclass
class Session:
    """Session state for a user."""
    sender: str
    locked: bool = False
    module_type: Optional[ModuleType] = None
    module_name: Optional[str] = None
    file_name: Optional[str] = None
    menu_id: Optional[int] = None
    service_instance: Optional[Any] = None
    entered_at: Optional[datetime] = None
    last_activity: datetime = field(default_factory=datetime.now)
    history: List[Dict[str, Any]] = field(default_factory=list)
    
    def update_activity(self):
        """Update last activity timestamp."""
        self.last_activity = datetime.now()
    
    def is_expired(self, timeout_seconds: int = SESSION_TIMEOUT_SECONDS) -> bool:
        """Check if session has expired."""
        elapsed = (datetime.now() - self.last_activity).total_seconds()
        return elapsed > timeout_seconds
    
    def add_history(self, query: str, response: str):
        """Add to conversation history."""
        self.history.append({
            "query": query,
            "response": response[:200] if len(response) > 200 else response,
            "timestamp": datetime.now().isoformat()
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]
    
    def lock(self, module_type: ModuleType, module_name: str, file_name: str, 
             menu_id: int, service_instance: Any):
        """Lock session to a module."""
        self.locked = True
        self.module_type = module_type
        self.module_name = module_name
        self.file_name = file_name
        self.menu_id = menu_id
        self.service_instance = service_instance
        self.entered_at = datetime.now()
        self.update_activity()
        logger.info(f"🔒 Session LOCKED: {self.sender} → {module_name}")
    
    def unlock(self):
        """Unlock session."""
        old_module = self.module_name
        self.locked = False
        self.module_type = None
        self.module_name = None
        self.file_name = None
        self.menu_id = None
        self.service_instance = None
        self.entered_at = None
        self.update_activity()
        logger.info(f"🔓 Session UNLOCKED: {self.sender} from {old_module}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dictionary."""
        return {
            "sender": self.sender,
            "locked": self.locked,
            "module_type": self.module_type.value if self.module_type else None,
            "module_name": self.module_name,
            "file_name": self.file_name,
            "menu_id": self.menu_id,
            "entered_at": self.entered_at.isoformat() if self.entered_at else None,
            "last_activity": self.last_activity.isoformat(),
            "history_count": len(self.history),
            "is_expired": self.is_expired()
        }

@dataclass
class MenuItem:
    """Menu item configuration."""
    id: int
    name: str
    aliases: List[str]
    module_type: ModuleType
    file: str
    loader: Callable
    
    def matches(self, text: str) -> bool:
        """Check if text matches this menu item."""
        text_lower = text.strip().lower()
        
        # Check by ID
        if text_lower == str(self.id):
            return True
        
        # Check by name
        if text_lower == self.name.lower():
            return True
        
        # Check by aliases
        for alias in self.aliases:
            if text_lower == alias.lower():
                return True
            if alias.lower() in text_lower:
                return True
        
        return False

# ============================================================
# BLOCK 4: SERVICE REGISTRY - ALL 7 SERVICES
# ============================================================

class ServiceRegistry:
    """
    SERVICE REGISTRY - All 7 modules registered here.
    Adding a new module requires only ONE configuration entry.
    """
    
    _instance: Optional["ServiceRegistry"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self._initialized = True
        self._menu_items: List[MenuItem] = []
        self._module_map: Dict[ModuleType, MenuItem] = {}
        self._loader_cache: Dict[ModuleType, Any] = {}
        self._cache_lock = threading.RLock()
        
        # Register all modules
        self._register_modules()
        
        logger.info(f"📦 Service Registry initialized with {len(self._menu_items)} modules")
    
    def _register_modules(self):
        """Register all 7 modules."""
        
        modules = [
            # Menu ID 1: National Dashboard → national_kpi_service.py
            MenuItem(
                id=1,
                name="National Dashboard",
                aliases=["national", "national kpi", "kpi", "pakistan", "overall", "dashboard"],
                module_type=ModuleType.NATIONAL,
                file="national_kpi_service.py",
                loader=self._load_national_service
            ),
            # Menu ID 2: DN Dashboard → dn_analysis.py (v31.0)
            MenuItem(
                id=2,
                name="DN Dashboard",
                aliases=["dn", "delivery", "delivery note", "pending dn", "delivery dashboard", "dn intelligence"],
                module_type=ModuleType.DN,
                file="dn_analysis.py",
                loader=self._load_dn_service
            ),
            # Menu ID 3: Dealer Dashboard → dealer_service.py
            MenuItem(
                id=3,
                name="Dealer Dashboard",
                aliases=["dealer", "distributor", "partner", "customer", "dealer analytics"],
                module_type=ModuleType.DEALER,
                file="dealer_service.py",
                loader=self._load_dealer_service
            ),
            # Menu ID 4: Warehouse Dashboard → warehouse_service.py
            MenuItem(
                id=4,
                name="Warehouse Dashboard",
                aliases=["warehouse", "storage", "plant", "inventory", "warehouse analytics"],
                module_type=ModuleType.WAREHOUSE,
                file="warehouse_service.py",
                loader=self._load_warehouse_service
            ),
            # Menu ID 5: Product Dashboard → product_service.py
            MenuItem(
                id=5,
                name="Product Dashboard",
                aliases=["product", "material", "sku", "model", "product analytics"],
                module_type=ModuleType.PRODUCT,
                file="product_service.py",
                loader=self._load_product_service
            ),
            # Menu ID 6: City Dashboard → city_service.py
            MenuItem(
                id=6,
                name="City Dashboard",
                aliases=["city", "location", "region", "city analytics", "town"],
                module_type=ModuleType.CITY,
                file="city_service.py",
                loader=self._load_city_service
            ),
            # Menu ID 7: AI Chat → groq_service.py
            MenuItem(
                id=7,
                name="AI Chat",
                aliases=["ai", "chat", "assistant", "groq", "ai assistant", "help"],
                module_type=ModuleType.AI,
                file="groq_service.py",
                loader=self._load_ai_service
            ),
        ]
        
        for item in modules:
            self._menu_items.append(item)
            self._module_map[item.module_type] = item
    
    # ============================================================
    # LOADER METHODS - All 7 services
    # ============================================================
    
    def _load_national_service(self):
        """Load National KPI service."""
        with self._cache_lock:
            if ModuleType.NATIONAL not in self._loader_cache:
                try:
                    from app.services.national_kpi_service import get_national_kpi_service
                    self._loader_cache[ModuleType.NATIONAL] = get_national_kpi_service()
                    logger.info("✅ National KPI service loaded")
                except Exception as e:
                    logger.error(f"❌ National KPI service load failed: {e}")
                    self._loader_cache[ModuleType.NATIONAL] = None
            return self._loader_cache[ModuleType.NATIONAL]
    
    def _load_dn_service(self):
        """Load DN service (v31.0)."""
        with self._cache_lock:
            if ModuleType.DN not in self._loader_cache:
                try:
                    from app.services.dn_analysis import get_dn_analysis_service
                    self._loader_cache[ModuleType.DN] = get_dn_analysis_service()
                    logger.info("✅ DN service loaded (v31.0)")
                except Exception as e:
                    logger.error(f"❌ DN service load failed: {e}")
                    self._loader_cache[ModuleType.DN] = None
            return self._loader_cache[ModuleType.DN]
    
    def _load_dealer_service(self):
        """Load Dealer service."""
        with self._cache_lock:
            if ModuleType.DEALER not in self._loader_cache:
                try:
                    from app.services.dealer_service import get_dealer_service
                    self._loader_cache[ModuleType.DEALER] = get_dealer_service()
                    logger.info("✅ Dealer service loaded")
                except Exception as e:
                    logger.error(f"❌ Dealer service load failed: {e}")
                    self._loader_cache[ModuleType.DEALER] = None
            return self._loader_cache[ModuleType.DEALER]
    
    def _load_warehouse_service(self):
        """Load Warehouse service."""
        with self._cache_lock:
            if ModuleType.WAREHOUSE not in self._loader_cache:
                try:
                    from app.services.warehouse_service import get_warehouse_analytics_service
                    self._loader_cache[ModuleType.WAREHOUSE] = get_warehouse_analytics_service()
                    logger.info("✅ Warehouse service loaded")
                except Exception as e:
                    logger.error(f"❌ Warehouse service load failed: {e}")
                    self._loader_cache[ModuleType.WAREHOUSE] = None
            return self._loader_cache[ModuleType.WAREHOUSE]
    
    def _load_product_service(self):
        """Load Product service."""
        with self._cache_lock:
            if ModuleType.PRODUCT not in self._loader_cache:
                try:
                    from app.services.product_service import get_product_analytics_service
                    self._loader_cache[ModuleType.PRODUCT] = get_product_analytics_service()
                    logger.info("✅ Product service loaded")
                except Exception as e:
                    logger.error(f"❌ Product service load failed: {e}")
                    self._loader_cache[ModuleType.PRODUCT] = None
            return self._loader_cache[ModuleType.PRODUCT]
    
    def _load_city_service(self):
        """Load City service."""
        with self._cache_lock:
            if ModuleType.CITY not in self._loader_cache:
                try:
                    from app.services.city_service import get_city_analytics_service
                    self._loader_cache[ModuleType.CITY] = get_city_analytics_service()
                    logger.info("✅ City service loaded")
                except Exception as e:
                    logger.error(f"❌ City service load failed: {e}")
                    self._loader_cache[ModuleType.CITY] = None
            return self._loader_cache[ModuleType.CITY]
    
    def _load_ai_service(self):
        """Load AI Chat service (Groq)."""
        with self._cache_lock:
            if ModuleType.AI not in self._loader_cache:
                try:
                    from app.services.groq_service import get_groq_service
                    self._loader_cache[ModuleType.AI] = get_groq_service()
                    logger.info("✅ AI Chat service loaded (Groq)")
                except Exception as e:
                    logger.error(f"❌ AI Chat service load failed: {e}")
                    self._loader_cache[ModuleType.AI] = None
            return self._loader_cache[ModuleType.AI]
    
    # ============================================================
    # PUBLIC METHODS
    # ============================================================
    
    def get_menu_items(self) -> List[MenuItem]:
        """Get all menu items."""
        return self._menu_items
    
    def get_menu_item_by_type(self, module_type: ModuleType) -> Optional[MenuItem]:
        """Get menu item by module type."""
        return self._module_map.get(module_type)
    
    def detect_menu_item(self, text: str) -> Optional[MenuItem]:
        """Detect which menu item the text matches."""
        text_clean = text.strip()
        
        for item in self._menu_items:
            if item.matches(text_clean):
                return item
        
        return None
    
    def get_service(self, module_type: ModuleType) -> Optional[Any]:
        """Get service instance for module type."""
        item = self._module_map.get(module_type)
        if not item:
            return None
        
        try:
            return item.loader()
        except Exception as e:
            logger.error(f"❌ Service load failed for {module_type.value}: {e}")
            return None
    
    def get_service_by_text(self, text: str) -> Optional[tuple[MenuItem, Any]]:
        """Get service by text detection."""
        item = self.detect_menu_item(text)
        if not item:
            return None
        
        service = self.get_service(item.module_type)
        if not service:
            return None
        
        return (item, service)

# ============================================================
# BLOCK 5: MAIN GATEWAY SERVICE
# ============================================================

class AIProviderService:
    """
    ENTERPRISE GATEWAY - SOLE ENTRY POINT
    
    This is the ONLY gateway for all WhatsApp interactions.
    
    Responsibilities:
    1. Manage sessions (lock/unlock)
    2. Route to modules (ALL 7 SERVICES)
    3. Forward messages when locked
    4. Show Main Dashboard
    5. Handle "__EXIT__" and "99" signals
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
        
        # Sessions storage
        self._sessions: Dict[str, Session] = {}
        self._session_lock = threading.RLock()
        
        # Service registry
        self._registry = ServiceRegistry()
        
        logger.info("=" * 70)
        logger.info("🚀 ENTERPRISE GATEWAY v50.0 initialized")
        logger.info("   📦 SOLE entry point for all interactions")
        logger.info("   🔒 GENERIC session locking (all modules equal)")
        logger.info("   🔀 Routes to any registered module")
        logger.info("   🚫 NO special-case logic")
        logger.info("   🚫 NO business logic")
        logger.info("   🚫 NO SQL queries")
        logger.info("   🚫 NO analytics")
        logger.info("   📋 Shows Main Dashboard")
        logger.info("   🚪 Only '__EXIT__' or '99' unlocks")
        logger.info("=" * 70)
        
        # Log available modules
        for item in self._registry.get_menu_items():
            logger.info(f"   {item.id}. {item.name} → {item.file}")
    
    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================
    
    def _get_session(self, sender: str) -> Session:
        """Get or create session for sender."""
        with self._session_lock:
            if sender not in self._sessions:
                self._sessions[sender] = Session(sender=sender)
                logger.info(f"🆕 New session created for {sender}")
                return self._sessions[sender]
            
            session = self._sessions[sender]
            
            # Check if session expired
            if session.is_expired():
                logger.info(f"⏰ Session expired for {sender}, creating new")
                del self._sessions[sender]
                session = Session(sender=sender)
                self._sessions[sender] = session
            
            return session
    
    def _lock_session(self, sender: str, menu_item: MenuItem, service_instance: Any) -> bool:
        """Lock session to a module."""
        with self._session_lock:
            session = self._get_session(sender)
            session.lock(
                module_type=menu_item.module_type,
                module_name=menu_item.name,
                file_name=menu_item.file,
                menu_id=menu_item.id,
                service_instance=service_instance
            )
            return True
    
    def _unlock_session(self, sender: str) -> bool:
        """Unlock session."""
        with self._session_lock:
            if sender not in self._sessions:
                return False
            
            session = self._sessions[sender]
            session.unlock()
            return True
    
    def _is_locked(self, sender: str) -> bool:
        """Check if session is locked."""
        with self._session_lock:
            if sender not in self._sessions:
                return False
            return self._sessions[sender].locked
    
    def _get_module_info(self, sender: str) -> Optional[Dict[str, Any]]:
        """Get current module info for locked session."""
        with self._session_lock:
            if sender not in self._sessions:
                return None
            
            session = self._sessions[sender]
            if not session.locked:
                return None
            
            return session.to_dict()
    
    # ============================================================
    # MODULE ROUTING - ALL 7 SERVICES
    # ============================================================
    
    def _detect_dashboard(self, message: str) -> Optional[tuple[MenuItem, Any]]:
        """
        Detect which dashboard the user wants.
        
        Supports:
        - Menu numbers: "1", "2", etc.
        - Dashboard names: "National Dashboard", "Warehouse Dashboard"
        - Aliases: "national", "warehouse", "pending dn"
        """
        return self._registry.get_service_by_text(message)
    
    def _forward_to_module(self, session: Session, message: str, sender: str) -> str:
        """Forward message to locked module."""
        if not session.service_instance:
            logger.error(f"❌ No service instance for {session.module_name}")
            self._unlock_session(sender)
            return self._get_main_dashboard()
        
        service = session.service_instance
        
        # Check if service has process_whatsapp_query method
        if not hasattr(service, "process_whatsapp_query"):
            logger.error(f"❌ Service {session.module_name} missing process_whatsapp_query")
            self._unlock_session(sender)
            return "⚠️ Service is misconfigured.\n\n" + self._get_main_dashboard()
        
        try:
            # Forward to service
            logger.info(f"📤 Forwarding to {session.module_name}: '{message}'")
            result = service.process_whatsapp_query(message, sender)
            
            # Check for exit signal
            if result == EXIT_SIGNAL or result == "99":
                logger.info(f"🚪 Module {session.module_name} requested exit ({result})")
                self._unlock_session(sender)
                return self._get_main_dashboard()
            
            # Update session activity
            session.update_activity()
            
            # Add to history
            session.add_history(message, result)
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Module {session.module_name} error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self._unlock_session(sender)
            return f"⚠️ Service error: {str(e)[:200]}\n\n" + self._get_main_dashboard()
    
    # ============================================================
    # MAIN PROCESSING - GATEWAY ONLY
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        MAIN ENTRY POINT - SOLE GATEWAY (SYNCHRONOUS)
        
        Flow:
        1. Get or create session
        2. Check if session is locked
        3. If locked → FORWARD to module (NO ROUTING)
        4. If unlocked → Show Main Dashboard or Route
        5. Handle commands → Show Main Dashboard
        6. Detect dashboard → Lock and Route
        7. No detection → Show Main Dashboard
        """
        if not message or not message.strip():
            return self._get_main_dashboard()
        
        message_clean = message.strip()
        logger.info(f"📨 Gateway received: '{message_clean}' from {sender}")
        
        # Get session
        session = self._get_session(sender)
        
        # ============================================================
        # STEP 1: CHECK IF SESSION IS LOCKED
        # ============================================================
        if session.locked:
            logger.info(f"🔒 Session LOCKED for {sender} → {session.module_name}")
            
            # Check for manual exit (99) at gateway level
            if message_clean == "99":
                logger.info(f"🚪 Manual exit (99) requested by {sender}")
                self._unlock_session(sender)
                return self._get_main_dashboard()
            
            # Forward to module
            return self._forward_to_module(session, message_clean, sender)
        
        # ============================================================
        # STEP 2: SESSION IDLE - CHECK COMMANDS
        # ============================================================
        logger.info(f"🔄 Session IDLE for {sender}")
        
        # Check for menu commands
        if message_clean.lower() in ["menu", "help", "options", "dashboard", "main", "0"]:
            return self._get_main_dashboard()
        
        # Check for exit
        if message_clean == "99":
            return self._get_main_dashboard()
        
        # ============================================================
        # STEP 3: DETECT DASHBOARD
        # ============================================================
        detected = self._detect_dashboard(message_clean)
        
        if detected:
            menu_item, service = detected
            logger.info(f"🎯 Detected: {menu_item.name} (ID: {menu_item.id})")
            
            # CRITICAL: Lock session BEFORE calling service
            self._lock_session(sender, menu_item, service)
            
            try:
                # Forward to service
                result = service.process_whatsapp_query(message_clean, sender)
                
                # Check for immediate exit
                if result == EXIT_SIGNAL or result == "99":
                    self._unlock_session(sender)
                    return self._get_main_dashboard()
                
                # Update session
                session = self._get_session(sender)
                session.update_activity()
                session.add_history(message_clean, result)
                
                return result
                
            except Exception as e:
                logger.error(f"❌ Module {menu_item.name} error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                self._unlock_session(sender)
                return f"⚠️ {menu_item.name} error: {str(e)[:200]}\n\n{self._get_main_dashboard()}"
        
        # ============================================================
        # STEP 4: NO DASHBOARD DETECTED - SHOW MAIN DASHBOARD
        # ============================================================
        return self._get_out_of_box_response()
    
    # ============================================================
    # RESPONSES
    # ============================================================
    
    def _get_main_dashboard(self) -> str:
        """Get the Main Dashboard - GENERIC from registry."""
        lines = ["🏠 *HPK Logistics AI*", ""]
        
        for item in self._registry.get_menu_items():
            lines.append(f"{item.id}️⃣ {item.name}")
        
        lines.extend([
            "",
            "📌 *Commands:*",
            "• Type a number (1-7) to enter a dashboard",
            "• Type dashboard name (e.g., 'Warehouse Dashboard')",
            "• Type '99' to exit current dashboard",
            "• Type 'menu' or 'help' for this menu",
            "",
            "Reply with a number or dashboard name:"
        ])
        
        return "\n".join(lines)
    
    def _get_out_of_box_response(self) -> str:
        """Response when no dashboard detected."""
        return "\n".join([
            "❌ *Please select a valid option from the menu.*",
            "",
            "You can enter a dashboard by:",
            "• Number (1-7)",
            "• Dashboard name (e.g., 'Warehouse Dashboard')",
            "",
            self._get_main_dashboard()
        ])
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for gateway."""
        with self._session_lock:
            active_sessions = len(self._sessions)
            locked_sessions = sum(1 for s in self._sessions.values() if s.locked)
            session_details = {
                sender: session.to_dict()
                for sender, session in self._sessions.items()
            }
        
        return {
            "service": "ai_provider_service",
            "version": "50.0",
            "type": "enterprise_gateway",
            "status": "healthy",
            "active_sessions": active_sessions,
            "locked_sessions": locked_sessions,
            "session_details": session_details,
            "available_modules": [
                {
                    "id": item.id,
                    "name": item.name,
                    "file": item.file,
                    "aliases": item.aliases
                }
                for item in self._registry.get_menu_items()
            ],
            "features": {
                "generic_session_locking": True,
                "generic_module_routing": True,
                "exit_signal": EXIT_SIGNAL,
                "main_dashboard": True,
                "alias_detection": True
            }
        }
    
    # ============================================================
    # SESSION MANAGEMENT UTILITIES
    # ============================================================
    
    def get_session_info(self, sender: str) -> Optional[Dict[str, Any]]:
        """Get session information for debugging."""
        with self._session_lock:
            if sender not in self._sessions:
                return None
            
            return self._sessions[sender].to_dict()
    
    def clear_session(self, sender: str) -> bool:
        """Clear session for debugging."""
        with self._session_lock:
            if sender in self._sessions:
                del self._sessions[sender]
                logger.info(f"🧹 Session cleared for {sender}")
                return True
            return False


# ============================================================
# BLOCK 6: SINGLETON
# ============================================================

_ai_service: Optional[AIProviderService] = None
_service_lock = threading.Lock()

def get_ai_provider_service() -> AIProviderService:
    """Get singleton instance."""
    global _ai_service
    if _ai_service is None:
        with _service_lock:
            if _ai_service is None:
                _ai_service = AIProviderService()
    return _ai_service


def process_whatsapp_query(message: str, sender: str = "default") -> str:
    """
    Process WhatsApp query through the gateway (SYNCHRONOUS).
    This is the main entry point for webhook calls.
    """
    try:
        service = get_ai_provider_service()
        return service.process_whatsapp_query(message, sender)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return "⚠️ Service is temporarily unavailable. Please try again."


# ============================================================
# BLOCK 7: EXPORTS
# ============================================================

__all__ = [
    "AIProviderService",
    "ModuleType",
    "Session",
    "MenuItem",
    "ServiceRegistry",
    "get_ai_provider_service",
    "process_whatsapp_query",
    "EXIT_SIGNAL",
]
