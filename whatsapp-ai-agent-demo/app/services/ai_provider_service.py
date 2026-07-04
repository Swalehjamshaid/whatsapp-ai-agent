# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 55.0 - GATEWAY WITH ONLY AVAILABLE SERVICES
# ============================================================

"""
File: app/services/ai_provider_service.py
Version: 55.0 - GATEWAY WITH ONLY AVAILABLE SERVICES

================================================================================
INTEGRATED SERVICES - ONLY THOSE THAT EXIST
================================================================================

Menu ID | Dashboard Name          | Service File           | Status
--------|-------------------------|------------------------|----------
1       | National Dashboard      | national_kpi_service.py| ✅ Available
2       | DN Dashboard            | dn_analysis.py         | ✅ Available
4       | Warehouse Dashboard     | warehouse_service.py   | ✅ Available
5       | Product Dashboard       | product_service.py     | ✅ Available
6       | City Dashboard          | city_service.py        | ✅ Available
7       | AI Assistant            | groq_service.py        | ✅ Available

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import logging
import os
import re
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

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
    """Menu item configuration."""
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
# BLOCK 4: SERVICE REGISTRY - ONLY AVAILABLE SERVICES
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
        
        self._register_modules()
        
        logger.info(f"📦 Service Registry initialized with {len(self._menu_items)} modules")
    
    def _register_modules(self):
        """Register only services that exist."""
        
        modules = []
        
        # Menu ID 1: National Dashboard
        if self._service_exists("national_kpi_service"):
            modules.append(
                MenuItem(
                    id=1,
                    name="National Dashboard",
                    aliases=["national", "national kpi", "kpi", "pakistan", "overall"],
                    module_type=ModuleType.NATIONAL,
                    file="national_kpi_service.py",
                    loader=self._load_national_service
                )
            )
            logger.info("✅ National Dashboard registered")
        else:
            logger.warning("⚠️ National Dashboard service not found - skipping")
        
        # Menu ID 2: DN Dashboard
        if self._service_exists("dn_analysis"):
            modules.append(
                MenuItem(
                    id=2,
                    name="DN Dashboard",
                    aliases=["dn", "dn dashboard", "delivery", "delivery note", "pending dn"],
                    module_type=ModuleType.DN,
                    file="dn_analysis.py",
                    loader=self._load_dn_service
                )
            )
            logger.info("✅ DN Dashboard registered")
        else:
            logger.warning("⚠️ DN Dashboard service not found - skipping")
        
        # Menu ID 4: Warehouse Dashboard
        if self._service_exists("warehouse_service"):
            modules.append(
                MenuItem(
                    id=4,
                    name="Warehouse Dashboard",
                    aliases=["warehouse", "warehouse dashboard", "warehouse analytics", "inventory"],
                    module_type=ModuleType.WAREHOUSE,
                    file="warehouse_service.py",
                    loader=self._load_warehouse_service
                )
            )
            logger.info("✅ Warehouse Dashboard registered")
        else:
            logger.warning("⚠️ Warehouse Dashboard service not found - skipping")
        
        # Menu ID 5: Product Dashboard
        if self._service_exists("product_service"):
            modules.append(
                MenuItem(
                    id=5,
                    name="Product Dashboard",
                    aliases=["product", "product dashboard", "material", "sku", "model"],
                    module_type=ModuleType.PRODUCT,
                    file="product_service.py",
                    loader=self._load_product_service
                )
            )
            logger.info("✅ Product Dashboard registered")
        else:
            logger.warning("⚠️ Product Dashboard service not found - skipping")
        
        # Menu ID 6: City Dashboard
        if self._service_exists("city_service"):
            modules.append(
                MenuItem(
                    id=6,
                    name="City Dashboard",
                    aliases=["city", "city dashboard", "location", "region"],
                    module_type=ModuleType.CITY,
                    file="city_service.py",
                    loader=self._load_city_service
                )
            )
            logger.info("✅ City Dashboard registered")
        else:
            logger.warning("⚠️ City Dashboard service not found - skipping")
        
        # Menu ID 7: AI Assistant (Groq)
        if self._service_exists("groq_service"):
            modules.append(
                MenuItem(
                    id=7,
                    name="AI Assistant",
                    aliases=["ai", "assistant", "chat", "help", "groq"],
                    module_type=ModuleType.AI,
                    file="groq_service.py",
                    loader=self._load_ai_service
                )
            )
            logger.info("✅ AI Assistant registered")
        else:
            logger.warning("⚠️ AI Assistant service not found - skipping")
        
        for item in modules:
            self._menu_items.append(item)
            self._module_map[item.module_type] = item
    
    def _service_exists(self, service_name: str) -> bool:
        """Check if a service exists."""
        try:
            # Try to import the service module
            module = __import__(f"app.services.{service_name}", fromlist=[""])
            return True
        except ImportError:
            return False
        except Exception:
            return False
    
    # ============================================================
    # LOADER METHODS - Only for services that exist
    # ============================================================
    
    def _safe_import(self, module_name: str, function_name: str) -> Optional[Any]:
        """Safely import a module and get a function."""
        try:
            module = __import__(module_name, fromlist=[function_name])
            return getattr(module, function_name, None)
        except ImportError as e:
            logger.debug(f"Could not import {module_name}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Error importing {module_name}: {e}")
            return None
    
    def _load_national_service(self):
        with self._cache_lock:
            if ModuleType.NATIONAL not in self._loader_cache:
                try:
                    from app.services.national_kpi_service import get_national_kpi_service
                    self._loader_cache[ModuleType.NATIONAL] = get_national_kpi_service()
                    logger.info("✅ National KPI service loaded")
                except Exception as e:
                    logger.error(f"❌ National KPI service init failed: {e}")
                    self._loader_cache[ModuleType.NATIONAL] = None
            return self._loader_cache[ModuleType.NATIONAL]
    
    def _load_dn_service(self):
        with self._cache_lock:
            if ModuleType.DN not in self._loader_cache:
                try:
                    from app.services.dn_analysis import get_dn_analysis_service
                    self._loader_cache[ModuleType.DN] = get_dn_analysis_service()
                    logger.info("✅ DN service loaded")
                except Exception as e:
                    logger.error(f"❌ DN service init failed: {e}")
                    self._loader_cache[ModuleType.DN] = None
            return self._loader_cache[ModuleType.DN]
    
    def _load_warehouse_service(self):
        with self._cache_lock:
            if ModuleType.WAREHOUSE not in self._loader_cache:
                try:
                    from app.services.warehouse_service import get_warehouse_analytics_service
                    self._loader_cache[ModuleType.WAREHOUSE] = get_warehouse_analytics_service()
                    logger.info("✅ Warehouse service loaded")
                except Exception as e:
                    logger.error(f"❌ Warehouse service init failed: {e}")
                    self._loader_cache[ModuleType.WAREHOUSE] = None
            return self._loader_cache[ModuleType.WAREHOUSE]
    
    def _load_product_service(self):
        with self._cache_lock:
            if ModuleType.PRODUCT not in self._loader_cache:
                try:
                    from app.services.product_service import get_product_analytics_service
                    self._loader_cache[ModuleType.PRODUCT] = get_product_analytics_service()
                    logger.info("✅ Product service loaded")
                except Exception as e:
                    logger.error(f"❌ Product service init failed: {e}")
                    self._loader_cache[ModuleType.PRODUCT] = None
            return self._loader_cache[ModuleType.PRODUCT]
    
    def _load_city_service(self):
        with self._cache_lock:
            if ModuleType.CITY not in self._loader_cache:
                try:
                    from app.services.city_service import get_city_analytics_service
                    self._loader_cache[ModuleType.CITY] = get_city_analytics_service()
                    logger.info("✅ City service loaded")
                except Exception as e:
                    logger.error(f"❌ City service init failed: {e}")
                    self._loader_cache[ModuleType.CITY] = None
            return self._loader_cache[ModuleType.CITY]
    
    def _load_ai_service(self):
        with self._cache_lock:
            if ModuleType.AI not in self._loader_cache:
                try:
                    from app.services.groq_service import get_groq_service
                    self._loader_cache[ModuleType.AI] = get_groq_service()
                    logger.info("✅ AI Assistant service loaded")
                except Exception as e:
                    logger.error(f"❌ AI Assistant service init failed: {e}")
                    self._loader_cache[ModuleType.AI] = None
            return self._loader_cache[ModuleType.AI]
    
    # ============================================================
    # PUBLIC METHODS
    # ============================================================
    
    def get_menu_items(self) -> List[MenuItem]:
        return self._menu_items
    
    def get_menu_item_by_type(self, module_type: ModuleType) -> Optional[MenuItem]:
        return self._module_map.get(module_type)
    
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
        try:
            return item.loader()
        except Exception as e:
            logger.error(f"❌ Service load failed for {module_type.value}: {e}")
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
# BLOCK 5: MAIN GATEWAY SERVICE
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
        logger.info("🚀 ENTERPRISE GATEWAY v55.0 initialized")
        logger.info(f"   📦 Registered {len(self._registry.get_menu_items())} services")
        logger.info("   🔒 Session Locking: ✅")
        logger.info("   🚫 NO business logic")
        logger.info("   🚫 NO SQL queries")
        logger.info("   🚫 NO analytics")
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
    # MAIN PROCESSING
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        try:
            logger.info(f"📨 Gateway received: '{message}' from {sender}")
            
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
    # RESPONSES
    # ============================================================
    
    def _get_main_dashboard(self) -> str:
        lines = ["🏠 *HPK Logistics AI*", ""]
        
        for item in self._registry.get_menu_items():
            lines.append(f"{item.id}️⃣ {item.name}")
        
        lines.extend([
            "",
            "📌 *Commands:*",
            "• Type a number to enter a dashboard",
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
            "• Number",
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
            "version": "55.0",
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
# BLOCK 6: SINGLETON
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


def process_whatsapp_query(message: str, sender: str = "default") -> str:
    try:
        service = get_ai_provider_service()
        return service.process_whatsapp_query(message, sender)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return "⚠️ Service is temporarily unavailable. Please try again later."


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
