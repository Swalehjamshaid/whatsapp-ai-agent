# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 43.0 - ENTERPRISE GATEWAY
# ============================================================

"""
File: app/services/ai_provider_service.py
Version: 43.0 - ENTERPRISE GATEWAY

================================================================================
PURPOSE
================================================================================

This is the SOLE GATEWAY for all WhatsApp interactions.

Its ONLY responsibilities are:
1. Detect if session is locked to a module
2. If locked → Forward EVERY message to that module (NO ROUTING)
3. If unlocked → Show Main Dashboard or Route to selected module
4. ONLY "99" unlocks the session and returns to Main Dashboard

================================================================================
ARCHITECTURE
================================================================================

WhatsApp
    │
    ▼
ai_provider_service.py (GATEWAY)
    │
    ├── Session Locked?
    │
    ├── YES
    │      │
    │      ▼
    │  Active Module
    │      │
    │      ▼
    │  Route Directly (NO ROUTING)
    │
    └── NO
           │
           ▼
    Main Dashboard
           │
           ▼
    Detect Dashboard
           │
           ▼
    Lock Session
           │
           ▼
    Route Once

================================================================================
DASHBOARD MAPPING
================================================================================

Number | Dashboard Name          | File
-------|-------------------------|------------------------------
1      | National Dashboard      | national_kpi_service.py
2      | DN Dashboard            | dn_analysis.py
3      | Dealer Dashboard        | dealer_service.py
4      | Warehouse Dashboard     | warehouse_service.py
5      | Product Dashboard       | product_service.py
6      | City Dashboard          | city_service.py
7      | Inventory Dashboard     | inventory_service.py
8      | PGI Dashboard           | pgi_service.py
9      | POD Dashboard           | pod_service.py
10     | Logistics Dashboard     | logistics_service.py

================================================================================
SESSION OBJECT
================================================================================

{
    "sender": "+923001234567",
    "locked": True,
    "module": "warehouse",
    "file": "warehouse_service.py",
    "entered_at": "2026-07-04T08:23:52",
    "last_activity": "2026-07-04T08:23:52",
    "history": []
}

================================================================================
EXIT RULE
================================================================================

The ONLY valid exit command is "99"

When received:
1. Module returns "__EXIT__"
2. Gateway unlocks session
3. Return Main Dashboard

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
from functools import lru_cache

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: CONFIGURATION
# ============================================================

SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "1800"))  # 30 minutes

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
    INVENTORY = "inventory"
    PGI = "pgi"
    POD = "pod"
    LOGISTICS = "logistics"
    MAIN = "main"

# ============================================================
# BLOCK 3: DATA CLASSES
# ============================================================

@dataclass
class Session:
    """Session state for a user."""
    sender: str
    locked: bool = False
    module: Optional[ModuleType] = None
    file: Optional[str] = None
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
            "response": response,
            "timestamp": datetime.now().isoformat()
        })
        # Keep last 100 entries
        if len(self.history) > 100:
            self.history = self.history[-100:]

@dataclass
class ModuleConfig:
    """Configuration for a domain module."""
    module_type: ModuleType
    file: str
    display_name: str
    number: int
    loader: Callable

# ============================================================
# BLOCK 4: MODULE LOADER
# ============================================================

class ModuleLoader:
    """Lazy load domain modules only when needed."""
    
    _instances: Dict[str, Any] = {}
    _lock = threading.Lock()
    
    @classmethod
    def get_national_service(cls):
        """Get or load National KPI service."""
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
    
    @classmethod
    def get_dn_service(cls):
        """Get or load DN service."""
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
        """Get or load Dealer service."""
        if "dealer" not in cls._instances:
            with cls._lock:
                if "dealer" not in cls._instances:
                    try:
                        from app.services.dealer_service import DealerService
                        cls._instances["dealer"] = DealerService()
                        logger.info("✅ Dealer service loaded")
                    except Exception as e:
                        logger.error(f"❌ Dealer service load failed: {e}")
                        cls._instances["dealer"] = None
        return cls._instances["dealer"]
    
    @classmethod
    def get_warehouse_service(cls):
        """Get or load Warehouse service."""
        if "warehouse" not in cls._instances:
            with cls._lock:
                if "warehouse" not in cls._instances:
                    try:
                        from app.services.warehouse_service import WarehouseService
                        cls._instances["warehouse"] = WarehouseService()
                        logger.info("✅ Warehouse service loaded")
                    except Exception as e:
                        logger.error(f"❌ Warehouse service load failed: {e}")
                        cls._instances["warehouse"] = None
        return cls._instances["warehouse"]
    
    @classmethod
    def get_product_service(cls):
        """Get or load Product service."""
        if "product" not in cls._instances:
            with cls._lock:
                if "product" not in cls._instances:
                    try:
                        from app.services.product_service import ProductService
                        cls._instances["product"] = ProductService()
                        logger.info("✅ Product service loaded")
                    except Exception as e:
                        logger.error(f"❌ Product service load failed: {e}")
                        cls._instances["product"] = None
        return cls._instances["product"]
    
    @classmethod
    def get_city_service(cls):
        """Get or load City service."""
        if "city" not in cls._instances:
            with cls._lock:
                if "city" not in cls._instances:
                    try:
                        from app.services.city_service import CityService
                        cls._instances["city"] = CityService()
                        logger.info("✅ City service loaded")
                    except Exception as e:
                        logger.error(f"❌ City service load failed: {e}")
                        cls._instances["city"] = None
        return cls._instances["city"]
    
    @classmethod
    def get_inventory_service(cls):
        """Get or load Inventory service."""
        if "inventory" not in cls._instances:
            with cls._lock:
                if "inventory" not in cls._instances:
                    try:
                        from app.services.inventory_service import InventoryService
                        cls._instances["inventory"] = InventoryService()
                        logger.info("✅ Inventory service loaded")
                    except Exception as e:
                        logger.error(f"❌ Inventory service load failed: {e}")
                        cls._instances["inventory"] = None
        return cls._instances["inventory"]
    
    @classmethod
    def get_pgi_service(cls):
        """Get or load PGI service."""
        if "pgi" not in cls._instances:
            with cls._lock:
                if "pgi" not in cls._instances:
                    try:
                        from app.services.pgi_service import PGIService
                        cls._instances["pgi"] = PGIService()
                        logger.info("✅ PGI service loaded")
                    except Exception as e:
                        logger.error(f"❌ PGI service load failed: {e}")
                        cls._instances["pgi"] = None
        return cls._instances["pgi"]
    
    @classmethod
    def get_pod_service(cls):
        """Get or load POD service."""
        if "pod" not in cls._instances:
            with cls._lock:
                if "pod" not in cls._instances:
                    try:
                        from app.services.pod_service import PODService
                        cls._instances["pod"] = PODService()
                        logger.info("✅ POD service loaded")
                    except Exception as e:
                        logger.error(f"❌ POD service load failed: {e}")
                        cls._instances["pod"] = None
        return cls._instances["pod"]
    
    @classmethod
    def get_logistics_service(cls):
        """Get or load Logistics service."""
        if "logistics" not in cls._instances:
            with cls._lock:
                if "logistics" not in cls._instances:
                    try:
                        from app.services.logistics_service import LogisticsService
                        cls._instances["logistics"] = LogisticsService()
                        logger.info("✅ Logistics service loaded")
                    except Exception as e:
                        logger.error(f"❌ Logistics service load failed: {e}")
                        cls._instances["logistics"] = None
        return cls._instances["logistics"]

# ============================================================
# BLOCK 5: MAIN GATEWAY SERVICE
# ============================================================

class AIProviderService:
    """
    ENTERPRISE GATEWAY - SOLE ENTRY POINT
    
    This is the ONLY gateway for all WhatsApp interactions.
    
    Responsibilities:
    1. Manage sessions (lock/unlock)
    2. Route to modules
    3. Forward messages when locked
    4. Show Main Dashboard
    5. Handle "99" exit
    
    NO business logic.
    NO SQL queries.
    NO analytics.
    NO answering questions directly.
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
        
        # Module loader
        self._loader = ModuleLoader()
        
        # Module configurations
        self._modules = {
            "1": ModuleConfig(
                module_type=ModuleType.NATIONAL,
                file="national_kpi_service.py",
                display_name="National Dashboard",
                number=1,
                loader=self._loader.get_national_service
            ),
            "2": ModuleConfig(
                module_type=ModuleType.DN,
                file="dn_analysis.py",
                display_name="DN Dashboard",
                number=2,
                loader=self._loader.get_dn_service
            ),
            "3": ModuleConfig(
                module_type=ModuleType.DEALER,
                file="dealer_service.py",
                display_name="Dealer Dashboard",
                number=3,
                loader=self._loader.get_dealer_service
            ),
            "4": ModuleConfig(
                module_type=ModuleType.WAREHOUSE,
                file="warehouse_service.py",
                display_name="Warehouse Dashboard",
                number=4,
                loader=self._loader.get_warehouse_service
            ),
            "5": ModuleConfig(
                module_type=ModuleType.PRODUCT,
                file="product_service.py",
                display_name="Product Dashboard",
                number=5,
                loader=self._loader.get_product_service
            ),
            "6": ModuleConfig(
                module_type=ModuleType.CITY,
                file="city_service.py",
                display_name="City Dashboard",
                number=6,
                loader=self._loader.get_city_service
            ),
            "7": ModuleConfig(
                module_type=ModuleType.INVENTORY,
                file="inventory_service.py",
                display_name="Inventory Dashboard",
                number=7,
                loader=self._loader.get_inventory_service
            ),
            "8": ModuleConfig(
                module_type=ModuleType.PGI,
                file="pgi_service.py",
                display_name="PGI Dashboard",
                number=8,
                loader=self._loader.get_pgi_service
            ),
            "9": ModuleConfig(
                module_type=ModuleType.POD,
                file="pod_service.py",
                display_name="POD Dashboard",
                number=9,
                loader=self._loader.get_pod_service
            ),
            "10": ModuleConfig(
                module_type=ModuleType.LOGISTICS,
                file="logistics_service.py",
                display_name="Logistics Dashboard",
                number=10,
                loader=self._loader.get_logistics_service
            ),
        }
        
        # Dashboard name to number mapping
        self._dashboard_names = {
            "national": "1",
            "national dashboard": "1",
            "dn": "2",
            "dn dashboard": "2",
            "delivery": "2",
            "dealer": "3",
            "dealer dashboard": "3",
            "warehouse": "4",
            "warehouse dashboard": "4",
            "product": "5",
            "product dashboard": "5",
            "city": "6",
            "city dashboard": "6",
            "inventory": "7",
            "inventory dashboard": "7",
            "pgi": "8",
            "pgi dashboard": "8",
            "pod": "9",
            "pod dashboard": "9",
            "logistics": "10",
            "logistics dashboard": "10",
        }
        
        logger.info("=" * 70)
        logger.info("🚀 ENTERPRISE GATEWAY v43.0 initialized")
        logger.info("   📦 SOLE entry point for all interactions")
        logger.info("   🔒 Manages session locking/unlocking")
        logger.info("   🔀 Routes to domain modules")
        logger.info("   🚫 NO business logic")
        logger.info("   🚫 NO SQL queries")
        logger.info("   🚫 NO analytics")
        logger.info("   📋 Shows Main Dashboard")
        logger.info("   🚪 Only '99' exits")
        logger.info("=" * 70)
        
        # Log available modules
        for key, config in self._modules.items():
            logger.info(f"   {key}. {config.display_name} → {config.file}")
    
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
                # Clean up expired session
                del self._sessions[sender]
                session = Session(sender=sender)
                self._sessions[sender] = session
            
            return session
    
    def _lock_session(self, sender: str, module_type: ModuleType, file: str) -> bool:
        """Lock session to a module."""
        with self._session_lock:
            if sender not in self._sessions:
                self._sessions[sender] = Session(sender=sender)
            
            session = self._sessions[sender]
            session.locked = True
            session.module = module_type
            session.file = file
            session.entered_at = datetime.now()
            session.update_activity()
            
            logger.info(f"🔒 Session LOCKED for {sender} → {module_type.value} ({file})")
            return True
    
    def _unlock_session(self, sender: str) -> bool:
        """Unlock session."""
        with self._session_lock:
            if sender not in self._sessions:
                return False
            
            session = self._sessions[sender]
            session.locked = False
            session.module = None
            session.file = None
            session.entered_at = None
            session.update_activity()
            
            logger.info(f"🔓 Session UNLOCKED for {sender}")
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
            
            return {
                "module": session.module.value if session.module else None,
                "file": session.file,
                "entered_at": session.entered_at.isoformat() if session.entered_at else None,
                "last_activity": session.last_activity.isoformat(),
                "history_count": len(session.history)
            }
    
    # ============================================================
    # MODULE ROUTING
    # ============================================================
    
    def _detect_dashboard(self, message: str) -> Optional[tuple[str, ModuleConfig]]:
        """Detect which dashboard the user wants."""
        message_clean = message.strip().lower()
        
        # Check by number
        if message_clean in self._modules:
            return (message_clean, self._modules[message_clean])
        
        # Check by name
        if message_clean in self._dashboard_names:
            number = self._dashboard_names[message_clean]
            return (number, self._modules[number])
        
        # Check for partial matches
        for name, number in self._dashboard_names.items():
            if name in message_clean:
                return (number, self._modules[number])
        
        return None
    
    def _get_module_service(self, module_config: ModuleConfig):
        """Get service instance for module."""
        try:
            return module_config.loader()
        except Exception as e:
            logger.error(f"❌ Module {module_config.display_name} load failed: {e}")
            return None
    
    def _forward_to_module(self, session: Session, message: str, sender: str) -> str:
        """Forward message to locked module."""
        if not session.file:
            return self._get_main_dashboard()
        
        # Get service based on module
        service = None
        if session.module == ModuleType.NATIONAL:
            service = self._loader.get_national_service()
        elif session.module == ModuleType.DN:
            service = self._loader.get_dn_service()
        elif session.module == ModuleType.DEALER:
            service = self._loader.get_dealer_service()
        elif session.module == ModuleType.WAREHOUSE:
            service = self._loader.get_warehouse_service()
        elif session.module == ModuleType.PRODUCT:
            service = self._loader.get_product_service()
        elif session.module == ModuleType.CITY:
            service = self._loader.get_city_service()
        elif session.module == ModuleType.INVENTORY:
            service = self._loader.get_inventory_service()
        elif session.module == ModuleType.PGI:
            service = self._loader.get_pgi_service()
        elif session.module == ModuleType.POD:
            service = self._loader.get_pod_service()
        elif session.module == ModuleType.LOGISTICS:
            service = self._loader.get_logistics_service()
        
        if not service:
            logger.error(f"❌ Service {session.module.value} not available")
            self._unlock_session(sender)
            return "⚠️ Service is temporarily unavailable.\n\n" + self._get_main_dashboard()
        
        # Check if service has process_whatsapp_query method
        if not hasattr(service, "process_whatsapp_query"):
            logger.error(f"❌ Service {session.module.value} missing process_whatsapp_query")
            self._unlock_session(sender)
            return "⚠️ Service is misconfigured.\n\n" + self._get_main_dashboard()
        
        try:
            # Forward to service
            result = service.process_whatsapp_query(message, sender)
            
            # Check for exit command
            if result == "__EXIT__" or result == "99":
                logger.info(f"🚪 Module {session.module.value} requested exit")
                self._unlock_session(sender)
                return self._get_main_dashboard()
            
            # Update session activity
            session.update_activity()
            
            # Add to history
            session.add_history(message, result[:200] if len(result) > 200 else result)
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Module {session.module.value} error: {e}")
            self._unlock_session(sender)
            return f"⚠️ Service error: {str(e)[:200]}\n\n" + self._get_main_dashboard()
    
    # ============================================================
    # MAIN PROCESSING - GATEWAY ONLY
    # ============================================================
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """
        MAIN ENTRY POINT - SOLE GATEWAY
        
        Flow:
        1. Get or create session
        2. Check if session is locked
        3. If locked → FORWARD to module (NO ROUTING)
        4. If unlocked → Show Main Dashboard or Route
        5. Handle "99" → Unlock and show Main Dashboard
        6. Route to selected module
        7. Lock session
        8. Return response
        """
        sender = sender or sender_id or "default"
        
        if not message or not message.strip():
            return self._get_main_dashboard()
        
        message_clean = message.strip()
        logger.info(f"📨 Gateway received: '{message_clean}' from {sender}")
        
        # Get session
        session = self._get_session(sender)
        
        # ============================================================
        # STEP 1: CHECK FOR EXIT COMMAND
        # ============================================================
        if message_clean == "99":
            logger.info(f"🚪 Exit requested by {sender}")
            self._unlock_session(sender)
            return self._get_main_dashboard()
        
        # ============================================================
        # STEP 2: CHECK IF SESSION IS LOCKED
        # ============================================================
        if session.locked:
            logger.info(f"🔒 Session LOCKED for {sender} → {session.module.value}")
            
            # Check if module wants to exit
            result = self._forward_to_module(session, message_clean, sender)
            
            # If result is the main dashboard, session was unlocked
            if "AI LOGISTICS MENU" in result or "Main Dashboard" in result:
                return result
            
            return result
        
        # ============================================================
        # STEP 3: SESSION IDLE - SHOW MAIN DASHBOARD OR ROUTE
        # ============================================================
        logger.info(f"🔄 Session IDLE for {sender}")
        
        # Check if user wants to see menu
        if message_clean.lower() in ["menu", "help", "options", "dashboard", "main"]:
            return self._get_main_dashboard()
        
        # ============================================================
        # STEP 4: DETECT DASHBOARD
        # ============================================================
        detected = self._detect_dashboard(message_clean)
        
        if detected:
            number, module_config = detected
            
            # Get service instance
            service = self._get_module_service(module_config)
            
            if not service:
                return f"⚠️ {module_config.display_name} is temporarily unavailable.\n\n{self._get_main_dashboard()}"
            
            # Check if service has process_whatsapp_query method
            if not hasattr(service, "process_whatsapp_query"):
                return f"⚠️ {module_config.display_name} is misconfigured.\n\n{self._get_main_dashboard()}"
            
            # Lock session
            self._lock_session(sender, module_config.module_type, module_config.file)
            
            try:
                # Forward to service
                result = service.process_whatsapp_query(message_clean, sender)
                
                # Check for immediate exit
                if result == "__EXIT__" or result == "99":
                    self._unlock_session(sender)
                    return self._get_main_dashboard()
                
                # Update session
                session = self._get_session(sender)
                session.update_activity()
                session.add_history(message_clean, result[:200] if len(result) > 200 else result)
                
                return result
                
            except Exception as e:
                logger.error(f"❌ Module {module_config.display_name} error: {e}")
                self._unlock_session(sender)
                return f"⚠️ {module_config.display_name} error: {str(e)[:200]}\n\n{self._get_main_dashboard()}"
        
        # ============================================================
        # STEP 5: NO DASHBOARD DETECTED - SHOW MAIN DASHBOARD
        # ============================================================
        return self._get_out_of_box_response()
    
    # ============================================================
    # RESPONSES
    # ============================================================
    
    def _get_main_dashboard(self) -> str:
        """Get the Main Dashboard."""
        return "\n".join([
            "🏠 *HPK Logistics AI*",
            "",
            "1️⃣ National Dashboard",
            "2️⃣ DN Intelligence",
            "3️⃣ Dealer Analytics",
            "4️⃣ Warehouse Analytics",
            "5️⃣ Product Analytics",
            "6️⃣ City Analytics",
            "7️⃣ Inventory Analytics",
            "8️⃣ PGI Analytics",
            "9️⃣ POD Analytics",
            "🔟 Logistics Analytics",
            "",
            "📌 *Commands:*",
            "• Type a number (1-10) to enter a dashboard",
            "• Type dashboard name (e.g., 'Warehouse Dashboard')",
            "• Type '99' to exit current dashboard",
            "• Type 'menu' or 'help' for this menu",
            "",
            "Reply with a number or dashboard name:"
        ])
    
    def _get_out_of_box_response(self) -> str:
        """Response when no dashboard detected."""
        return "\n".join([
            "❌ *Please select a valid option from the menu.*",
            "",
            "You can enter a dashboard by:",
            "• Number (1-10)",
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
                sender: {
                    "locked": session.locked,
                    "module": session.module.value if session.module else None,
                    "file": session.file,
                    "last_activity": session.last_activity.isoformat(),
                    "history_count": len(session.history)
                }
                for sender, session in self._sessions.items()
            }
        
        return {
            "service": "ai_provider_service",
            "version": "43.0",
            "type": "enterprise_gateway",
            "status": "healthy",
            "active_sessions": active_sessions,
            "locked_sessions": locked_sessions,
            "session_details": session_details,
            "available_modules": [
                {
                    "number": config.number,
                    "name": config.display_name,
                    "file": config.file
                }
                for config in self._modules.values()
            ],
            "features": {
                "session_locking": True,
                "module_routing": True,
                "exit_99": True,
                "main_dashboard": True
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
            
            session = self._sessions[sender]
            return {
                "sender": session.sender,
                "locked": session.locked,
                "module": session.module.value if session.module else None,
                "file": session.file,
                "entered_at": session.entered_at.isoformat() if session.entered_at else None,
                "last_activity": session.last_activity.isoformat(),
                "is_expired": session.is_expired(),
                "history_count": len(session.history),
                "recent_history": session.history[-5:] if session.history else []
            }
    
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

async def process_whatsapp_query(
    message: str,
    sender: Optional[str] = None,
    sender_id: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Process WhatsApp query through the gateway."""
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
    "ModuleType",
    "Session",
    "get_ai_provider_service",
    "process_whatsapp_query",
]
