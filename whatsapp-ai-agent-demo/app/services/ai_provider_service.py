# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 63.0 - FULL DN SERVICE INTEGRATION
# ============================================================

"""
File: app/services/ai_provider_service.py
Version: 63.0 - FULL DN SERVICE INTEGRATION

================================================================================
INTEGRATED SERVICES
================================================================================

Menu | Dashboard Name          | Route To                    | Function
-----|-------------------------|-----------------------------|-------------------------------
1    | National Dashboard      | national_kpi_service.py     | get_national_kpi_service()
2    | DN Intelligence Center  | dn_analysis.py              | get_dn_analytics_service()
3    | Dealer Dashboard        | dealer_analytics_service.py | get_dealer_service()
4    | Warehouse Dashboard     | warehouse_service.py        | get_warehouse_analytics_service()
5    | Product Dashboard       | product_service.py          | get_product_analytics_service()
6    | City Dashboard          | city_service.py             | get_city_analytics_service()
7    | AI Assistant            | groq_service.py             | get_groq_service()

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "1800"))  # 30 minutes
EXIT_SIGNAL = "__EXIT__"

# ============================================================
# ENUMS
# ============================================================

class ModuleType(Enum):
    """Available domain modules - EXACTLY 7"""
    NATIONAL = "national"
    DN = "dn"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    PRODUCT = "product"
    CITY = "city"
    AI = "ai"

# ============================================================
# DATA CLASSES
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
        self.last_activity = datetime.now()
    
    def is_expired(self, timeout_seconds: int = SESSION_TIMEOUT_SECONDS) -> bool:
        elapsed = (datetime.now() - self.last_activity).total_seconds()
        return elapsed > timeout_seconds
    
    def add_history(self, query: str, response: str):
        self.history.append({
            "query": query,
            "response": response[:200] if len(response) > 200 else response,
            "timestamp": datetime.now().isoformat()
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]
    
    def lock(self, module_type: ModuleType, module_name: str, file_name: str, 
             menu_id: int, service_instance: Any):
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
    """Menu item configuration - EXACTLY 7"""
    id: int
    name: str
    aliases: List[str]
    module_type: ModuleType
    file: str
    loader: Callable
    
    def matches(self, text: str) -> bool:
        text_lower = text.strip().lower()
        if text_lower == str(self.id):
            return True
        if text_lower == self.name.lower():
            return True
        for alias in self.aliases:
            if text_lower == alias.lower():
                return True
            if alias.lower() in text_lower:
                return True
        return False

# ============================================================
# SERVICE REGISTRY - ALL 7 SERVICES WITH CORRECT PATHS
# ============================================================

class ServiceRegistry:
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
        
        self._register_all_modules()
        
        logger.info(f"📦 Service Registry initialized with {len(self._menu_items)} modules")
    
    def _register_all_modules(self):
        """Register ALL 7 modules with correct import paths."""
        
        # Define all 7 modules
        module_defs = [
            {
                "id": 1,
                "name": "National Dashboard",
                "aliases": ["national", "national kpi", "kpi", "pakistan", "overall"],
                "module_type": ModuleType.NATIONAL,
                "file": "national_kpi_service.py",
                "import_path": "app.services.national_kpi_service",
                "function": "get_national_kpi_service"
            },
            {
                "id": 2,
                "name": "DN Intelligence Center",
                "aliases": ["dn", "dn dashboard", "dn intelligence", "delivery", "delivery note", "pending dn"],
                "module_type": ModuleType.DN,
                "file": "dn_analysis.py",
                "import_path": "app.services.dn_analysis",
                "function": "get_dn_analytics_service"
            },
            {
                "id": 3,
                "name": "Dealer Dashboard",
                "aliases": ["dealer", "dealer dashboard", "dealer analytics", "distributor"],
                "module_type": ModuleType.DEALER,
                "file": "dealer_analytics_service.py",
                "import_path": "app.services.dealer_analytics_service",
                "function": "get_dealer_service"
            },
            {
                "id": 4,
                "name": "Warehouse Dashboard",
                "aliases": ["warehouse", "warehouse dashboard", "warehouse analytics", "warehouse report"],
                "module_type": ModuleType.WAREHOUSE,
                "file": "warehouse_service.py",
                "import_path": "app.services.warehouse_service",
                "function": "get_warehouse_analytics_service"
            },
            {
                "id": 5,
                "name": "Product Dashboard",
                "aliases": ["product", "product dashboard", "material", "sku", "model"],
                "module_type": ModuleType.PRODUCT,
                "file": "product_service.py",
                "import_path": "app.services.product_service",
                "function": "get_product_analytics_service"
            },
            {
                "id": 6,
                "name": "City Dashboard",
                "aliases": ["city", "city dashboard", "location", "region"],
                "module_type": ModuleType.CITY,
                "file": "city_service.py",
                "import_path": "app.services.city_service",
                "function": "get_city_analytics_service"
            },
            {
                "id": 7,
                "name": "AI Assistant",
                "aliases": ["ai", "assistant", "general ai", "chat", "help"],
                "module_type": ModuleType.AI,
                "file": "groq_service.py",
                "import_path": "app.services.groq_service",
                "function": "get_groq_service"
            },
        ]
        
        for mod in module_defs:
            try:
                logger.info(f"🔍 Registering: {mod['name']}...")
                
                # Try to import the module
                module = __import__(mod['import_path'], fromlist=[mod['function']])
                loader_func = getattr(module, mod['function'], None)
                
                if loader_func:
                    menu_item = MenuItem(
                        id=mod['id'],
                        name=mod['name'],
                        aliases=mod['aliases'],
                        module_type=mod['module_type'],
                        file=mod['file'],
                        loader=loader_func
                    )
                    self._menu_items.append(menu_item)
                    self._module_map[mod['module_type']] = menu_item
                    logger.info(f"✅ Registered: {mod['id']}. {mod['name']} → {mod['file']}")
                else:
                    logger.warning(f"⚠️ Skipping: {mod['name']} - function {mod['function']} not found")
                    
            except ImportError as e:
                logger.warning(f"⚠️ Skipping: {mod['name']} - module not found: {e}")
            except Exception as e:
                logger.warning(f"⚠️ Skipping: {mod['name']} - error: {e}")
    
    def get_menu_items(self) -> List[MenuItem]:
        return self._menu_items
    
    def detect_menu_item(self, text: str) -> Optional[MenuItem]:
        text_clean = text.strip()
        for item in self._menu_items:
            if item.matches(text_clean):
                return item
        return None
    
    def get_service(self, module_type: ModuleType) -> Optional[Any]:
        item = self._module_map.get(module_type)
        if not item:
            return None
        
        if module_type in self._loader_cache:
            return self._loader_cache[module_type]
        
        try:
            service = item.loader()
            self._loader_cache[module_type] = service
            logger.info(f"✅ Service loaded: {item.name}")
            return service
        except Exception as e:
            logger.error(f"❌ Failed to load {item.name}: {e}")
            self._loader_cache[module_type] = None
            return None
    
    def get_service_by_text(self, text: str) -> Optional[tuple[MenuItem, Any]]:
        try:
            item = self.detect_menu_item(text)
            if not item:
                return None
            
            service = self.get_service(item.module_type)
            if not service:
                return None
            
            return (item, service)
        except Exception as e:
            logger.error(f"❌ get_service_by_text error: {e}")
            return None

# ============================================================
# MAIN GATEWAY SERVICE
# ============================================================

class AIProviderService:
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
        
        self._sessions: Dict[str, Session] = {}
        self._session_lock = threading.RLock()
        self._registry = ServiceRegistry()
        
        logger.info("=" * 70)
        logger.info("🚀 ENTERPRISE GATEWAY v63.0 initialized")
        logger.info(f"   📦 Registered {len(self._registry.get_menu_items())} services")
        logger.info("   🔒 Session Locking: ✅")
        logger.info("   🔀 Routes to 7 modules")
        logger.info("   🌐 Async compatible")
        logger.info("=" * 70)
        
        for item in self._registry.get_menu_items():
            logger.info(f"   {item.id}. {item.name} → {item.file}")
    
    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================
    
    def _get_session(self, sender: str) -> Session:
        with self._session_lock:
            if sender not in self._sessions:
                self._sessions[sender] = Session(sender=sender)
                logger.info(f"🆕 New session created for {sender}")
                return self._sessions[sender]
            
            session = self._sessions[sender]
            if session.is_expired():
                logger.info(f"⏰ Session expired for {sender}, creating new")
                del self._sessions[sender]
                session = Session(sender=sender)
                self._sessions[sender] = session
            
            return session
    
    def _lock_session(self, sender: str, menu_item: MenuItem, service_instance: Any) -> bool:
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
        with self._session_lock:
            if sender not in self._sessions:
                return False
            session = self._sessions[sender]
            session.unlock()
            return True
    
    def _is_locked(self, sender: str) -> bool:
        with self._session_lock:
            if sender not in self._sessions:
                return False
            return self._sessions[sender].locked
    
    # ============================================================
    # ROUTING
    # ============================================================
    
    def _detect_dashboard(self, message: str) -> Optional[tuple[MenuItem, Any]]:
        try:
            return self._registry.get_service_by_text(message)
        except Exception as e:
            logger.error(f"❌ Dashboard detection error: {e}")
            return None
    
    def _forward_to_module(self, session: Session, message: str, sender: str) -> str:
        if not session.service_instance:
            logger.error(f"❌ No service instance for {session.module_name}")
            self._unlock_session(sender)
            return self._get_main_dashboard()
        
        service = session.service_instance
        
        if not hasattr(service, "process_whatsapp_query"):
            logger.error(f"❌ Service {session.module_name} missing process_whatsapp_query")
            self._unlock_session(sender)
            return "⚠️ Service is misconfigured.\n\n" + self._get_main_dashboard()
        
        try:
            logger.info(f"📤 Forwarding to {session.module_name}: '{message}'")
            result = service.process_whatsapp_query(message, sender)
            
            # Check for exit signal
            if result == EXIT_SIGNAL or result == "99":
                logger.info(f"🚪 Module {session.module_name} requested exit")
                self._unlock_session(sender)
                return self._get_main_dashboard()
            
            session.update_activity()
            session.add_history(message, result)
            return result
            
        except Exception as e:
            logger.error(f"❌ Module {session.module_name} error: {e}")
            logger.error(traceback.format_exc())
            self._unlock_session(sender)
            return f"⚠️ Service error: {str(e)[:200]}\n\n" + self._get_main_dashboard()
    
    # ============================================================
    # MAIN PROCESSING - SYNC ENTRY POINT
    # ============================================================
    
    def process_whatsapp_query_sync(self, message: str, sender: str = "default") -> str:
        """SYNC entry point - for internal use."""
        try:
            logger.info(f"📨 Gateway (sync) received: '{message}' from {sender}")
            
            if not message or not message.strip():
                return self._get_main_dashboard()
            
            message_clean = message.strip()
            session = self._get_session(sender)
            
            # STEP 1: CHECK IF SESSION IS LOCKED
            if session.locked:
                logger.info(f"🔒 Session LOCKED for {sender} → {session.module_name}")
                
                if message_clean == "99":
                    logger.info(f"🚪 Manual exit (99) requested by {sender}")
                    self._unlock_session(sender)
                    return self._get_main_dashboard()
                
                return self._forward_to_module(session, message_clean, sender)
            
            # STEP 2: SESSION IDLE - CHECK COMMANDS
            logger.info(f"🔄 Session IDLE for {sender}")
            
            if message_clean.lower() in ["menu", "help", "options", "dashboard", "main", "0"]:
                return self._get_main_dashboard()
            
            # STEP 3: DETECT DASHBOARD
            detected = self._detect_dashboard(message_clean)
            
            if detected:
                menu_item, service = detected
                logger.info(f"🎯 Detected: {menu_item.name} (ID: {menu_item.id})")
                
                self._lock_session(sender, menu_item, service)
                
                try:
                    result = service.process_whatsapp_query(message_clean, sender)
                    
                    if result == EXIT_SIGNAL or result == "99":
                        logger.info(f"🚪 Immediate exit from {menu_item.name}")
                        self._unlock_session(sender)
                        return self._get_main_dashboard()
                    
                    session = self._get_session(sender)
                    session.update_activity()
                    session.add_history(message_clean, result)
                    return result
                    
                except Exception as e:
                    logger.error(f"❌ Module {menu_item.name} error: {e}")
                    logger.error(traceback.format_exc())
                    self._unlock_session(sender)
                    return f"⚠️ {menu_item.name} error: {str(e)[:200]}\n\n{self._get_main_dashboard()}"
            
            # STEP 4: NO DASHBOARD DETECTED
            return self._get_out_of_box_response()
            
        except Exception as e:
            logger.error(f"❌ Gateway error: {e}")
            logger.error(traceback.format_exc())
            return f"⚠️ System error: {str(e)[:200]}\n\n{self._get_main_dashboard()}"
    
    # ============================================================
    # ASYNC ENTRY POINT - FOR WEBHOOK
    # ============================================================
    
    async def process_whatsapp_query_async(self, message: str, sender: str = "default") -> str:
        """
        ASYNC entry point - Called by webhook.
        """
        try:
            logger.info(f"📨 Gateway (async) received: '{message}' from {sender}")
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self.process_whatsapp_query_sync,
                message,
                sender
            )
            
            logger.info(f"📤 Gateway (async) returning: {result[:100] if result else 'Empty'}...")
            return result
            
        except Exception as e:
            logger.error(f"❌ Gateway (async) error: {e}")
            logger.error(traceback.format_exc())
            return "⚠️ Service is temporarily unavailable. Please try again later."
    
    # ============================================================
    # RESPONSES
    # ============================================================
    
    def _get_main_dashboard(self) -> str:
        lines = ["🏠 *HPK Logistics AI*", ""]
        
        # Show ALL registered modules in order
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
        with self._session_lock:
            active_sessions = len(self._sessions)
            locked_sessions = sum(1 for s in self._sessions.values() if s.locked)
        
        return {
            "service": "ai_provider_service",
            "version": "63.0",
            "type": "enterprise_gateway",
            "status": "healthy",
            "active_sessions": active_sessions,
            "locked_sessions": locked_sessions,
            "available_modules": [
                {
                    "id": item.id,
                    "name": item.name,
                    "file": item.file,
                    "aliases": item.aliases
                }
                for item in self._registry.get_menu_items()
            ]
        }


# ============================================================
# SINGLETON
# ============================================================

_ai_service: Optional[AIProviderService] = None
_service_lock = threading.Lock()

def get_ai_provider_service() -> AIProviderService:
    global _ai_service
    if _ai_service is None:
        with _service_lock:
            if _ai_service is None:
                _ai_service = AIProviderService()
    return _ai_service


# ============================================================
# ENTRY POINT - FOR WEBHOOK
# ============================================================

async def process_whatsapp_query(message: str, sender: str = "default") -> str:
    """
    MAIN ENTRY POINT - Called by webhook.
    
    This is the function that the webhook calls:
    response = await process_whatsapp_query(text, sender)
    """
    try:
        logger.info(f"📨 process_whatsapp_query called: '{message}' from {sender}")
        service = get_ai_provider_service()
        return await service.process_whatsapp_query_async(message, sender)
    except Exception as e:
        logger.exception(f"Unexpected error in process_whatsapp_query: {e}")
        return "⚠️ Service is temporarily unavailable. Please try again later."


# ============================================================
# EXPORTS
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
