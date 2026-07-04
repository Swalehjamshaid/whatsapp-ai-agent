# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 44.0 - COMPLETE GENERIC ENTERPRISE GATEWAY
# ============================================================

"""
File: app/services/ai_provider_service.py
Version: 44.0 - COMPLETE GENERIC ENTERPRISE GATEWAY

================================================================================
PURPOSE
================================================================================

This is the SOLE GATEWAY for all WhatsApp interactions.

Its ONLY responsibilities are:
1. Detect if session is locked to a module
2. If locked → Forward EVERY message to that module (NO ROUTING)
3. If unlocked → Show Main Dashboard or Route to selected module
4. ONLY "__EXIT__" unlocks the session and returns to Main Dashboard

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
    Detect Dashboard (by number, name, or alias)
           │
           ▼
    Lock Session
           │
           ▼
    Route Once

================================================================================
GENERIC SERVICE REGISTRY
================================================================================

All modules are registered in SERVICE_REGISTRY.
Adding a new module requires only ONE configuration entry.

No special-case logic for any module.
Every module is treated identically.

================================================================================
EXIT CONTRACT
================================================================================

All modules MUST return "__EXIT__" to unlock the session.
This is the ONLY exit mechanism.

The gateway never checks for "99" in content.
It only checks for the exact "__EXIT__" string.

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
from typing import Any, Dict, List, Optional, Union, Callable, Type
from functools import lru_cache

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
    """Available domain modules - GENERIC"""
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
    SALES_OFFICE = "sales_office"
    TRANSPORT = "transport"
    FORECAST = "forecast"
    REPORTS = "reports"
    MANAGEMENT = "management"

# ============================================================
# BLOCK 3: DATA CLASSES
# ============================================================

@dataclass
class Session:
    """Session state for a user - GENERIC."""
    sender: str
    locked: bool = False
    module_type: Optional[ModuleType] = None
    module_name: Optional[str] = None
    file_name: Optional[str] = None
    menu_id: Optional[int] = None
    dashboard_name: Optional[str] = None
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
        # Keep last 100 entries
        if len(self.history) > 100:
            self.history = self.history[-100:]
    
    def lock(self, module_type: ModuleType, module_name: str, file_name: str, 
             menu_id: int, dashboard_name: str, service_instance: Any):
        """Lock session to a module."""
        self.locked = True
        self.module_type = module_type
        self.module_name = module_name
        self.file_name = file_name
        self.menu_id = menu_id
        self.dashboard_name = dashboard_name
        self.service_instance = service_instance
        self.entered_at = datetime.now()
        self.update_activity()
    
    def unlock(self):
        """Unlock session."""
        self.locked = False
        self.module_type = None
        self.module_name = None
        self.file_name = None
        self.menu_id = None
        self.dashboard_name = None
        self.service_instance = None
        self.entered_at = None
        self.update_activity()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dictionary."""
        return {
            "sender": self.sender,
            "locked": self.locked,
            "module_type": self.module_type.value if self.module_type else None,
            "module_name": self.module_name,
            "file_name": self.file_name,
            "menu_id": self.menu_id,
            "dashboard_name": self.dashboard_name,
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
# BLOCK 4: GENERIC SERVICE REGISTRY
# ============================================================

class ServiceRegistry:
    """
    GENERIC SERVICE REGISTRY
    
    All modules are registered here.
    Adding a new module requires only ONE configuration entry.
    No special-case logic anywhere.
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
        """Register all modules - GENERIC."""
        
        # ============================================================
        # Define all modules here - This is the ONLY place to add modules
        # ============================================================
        
        modules = [
            MenuItem(
                id=1,
                name="National Dashboard",
                aliases=["national", "national kpi", "kpi"],
                module_type=ModuleType.NATIONAL,
                file="national_kpi_service.py",
                loader=self._load_national_service
            ),
            MenuItem(
                id=2,
                name="DN Dashboard",
                aliases=["dn", "delivery", "delivery note", "pending dn"],
                module_type=ModuleType.DN,
                file="dn_analysis.py",
                loader=self._load_dn_service
            ),
            MenuItem(
                id=3,
                name="Dealer Dashboard",
                aliases=["dealer", "distributor", "partner"],
                module_type=ModuleType.DEALER,
                file="dealer_service.py",
                loader=self._load_dealer_service
            ),
            MenuItem(
                id=4,
                name="Warehouse Dashboard",
                aliases=["warehouse", "storage", "plant", "inventory"],
                module_type=ModuleType.WAREHOUSE,
                file="warehouse_service.py",
                loader=self._load_warehouse_service
            ),
            MenuItem(
                id=5,
                name="Product Dashboard",
                aliases=["product", "material", "sku"],
                module_type=ModuleType.PRODUCT,
                file="product_service.py",
                loader=self._load_product_service
            ),
            MenuItem(
                id=6,
                name="City Dashboard",
                aliases=["city", "location", "region"],
                module_type=ModuleType.CITY,
                file="city_service.py",
                loader=self._load_city_service
            ),
            MenuItem(
                id=7,
                name="Inventory Dashboard",
                aliases=["inventory", "stock", "availability"],
                module_type=ModuleType.INVENTORY,
                file="inventory_service.py",
                loader=self._load_inventory_service
            ),
            MenuItem(
                id=8,
                name="PGI Dashboard",
                aliases=["pgi", "goods issue", "issue"],
                module_type=ModuleType.PGI,
                file="pgi_service.py",
                loader=self._load_pgi_service
            ),
            MenuItem(
                id=9,
                name="POD Dashboard",
                aliases=["pod", "proof of delivery", "delivered"],
                module_type=ModuleType.POD,
                file="pod_service.py",
                loader=self._load_pod_service
            ),
            MenuItem(
                id=10,
                name="Logistics Dashboard",
                aliases=["logistics", "transport", "shipping", "fleet"],
                module_type=ModuleType.LOGISTICS,
                file="logistics_service.py",
                loader=self._load_logistics_service
            ),
            MenuItem(
                id=11,
                name="Sales Office Dashboard",
                aliases=["sales", "office", "sales office"],
                module_type=ModuleType.SALES_OFFICE,
                file="sales_office_service.py",
                loader=self._load_sales_office_service
            ),
            MenuItem(
                id=12,
                name="Transport Dashboard",
                aliases=["transport", "fleet", "vehicle"],
                module_type=ModuleType.TRANSPORT,
                file="transport_service.py",
                loader=self._load_transport_service
            ),
            MenuItem(
                id=13,
                name="Forecast Dashboard",
                aliases=["forecast", "prediction", "trend"],
                module_type=ModuleType.FORECAST,
                file="forecast_service.py",
                loader=self._load_forecast_service
            ),
            MenuItem(
                id=14,
                name="Reports Dashboard",
                aliases=["reports", "analytics", "insights"],
                module_type=ModuleType.REPORTS,
                file="reports_service.py",
                loader=self._load_reports_service
            ),
            MenuItem(
                id=15,
                name="Management Dashboard",
                aliases=["management", "executive", "dashboard"],
                module_type=ModuleType.MANAGEMENT,
                file="management_service.py",
                loader=self._load_management_service
            ),
        ]
        
        # Register all modules
        for item in modules:
            self._menu_items.append(item)
            self._module_map[item.module_type] = item
    
    # ============================================================
    # LOADER METHODS - GENERIC
    # ============================================================
    
    def _load_national_service(self):
        """Load National KPI service."""
        with self._cache_lock:
            if ModuleType.NATIONAL not in self._loader_cache:
                try:
                    from app.services.national_kpi_service import NationalKPIService
                    self._loader_cache[ModuleType.NATIONAL] = NationalKPIService()
                    logger.info("✅ National KPI service loaded")
                except Exception as e:
                    logger.error(f"❌ National KPI service load failed: {e}")
                    self._loader_cache[ModuleType.NATIONAL] = None
            return self._loader_cache[ModuleType.NATIONAL]
    
    def _load_dn_service(self):
        """Load DN service."""
        with self._cache_lock:
            if ModuleType.DN not in self._loader_cache:
                try:
                    from app.services.dn_analysis import DNAnalysisService
                    self._loader_cache[ModuleType.DN] = DNAnalysisService()
                    logger.info("✅ DN service loaded")
                except Exception as e:
                    logger.error(f"❌ DN service load failed: {e}")
                    self._loader_cache[ModuleType.DN] = None
            return self._loader_cache[ModuleType.DN]
    
    def _load_dealer_service(self):
        """Load Dealer service."""
        with self._cache_lock:
            if ModuleType.DEALER not in self._loader_cache:
                try:
                    from app.services.dealer_service import DealerService
                    self._loader_cache[ModuleType.DEALER] = DealerService()
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
                    from app.services.warehouse_service import WarehouseService
                    self._loader_cache[ModuleType.WAREHOUSE] = WarehouseService()
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
                    from app.services.product_service import ProductService
                    self._loader_cache[ModuleType.PRODUCT] = ProductService()
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
                    from app.services.city_service import CityService
                    self._loader_cache[ModuleType.CITY] = CityService()
                    logger.info("✅ City service loaded")
                except Exception as e:
                    logger.error(f"❌ City service load failed: {e}")
                    self._loader_cache[ModuleType.CITY] = None
            return self._loader_cache[ModuleType.CITY]
    
    def _load_inventory_service(self):
        """Load Inventory service."""
        with self._cache_lock:
            if ModuleType.INVENTORY not in self._loader_cache:
                try:
                    from app.services.inventory_service import InventoryService
                    self._loader_cache[ModuleType.INVENTORY] = InventoryService()
                    logger.info("✅ Inventory service loaded")
                except Exception as e:
                    logger.error(f"❌ Inventory service load failed: {e}")
                    self._loader_cache[ModuleType.INVENTORY] = None
            return self._loader_cache[ModuleType.INVENTORY]
    
    def _load_pgi_service(self):
        """Load PGI service."""
        with self._cache_lock:
            if ModuleType.PGI not in self._loader_cache:
                try:
                    from app.services.pgi_service import PGIService
                    self._loader_cache[ModuleType.PGI] = PGIService()
                    logger.info("✅ PGI service loaded")
                except Exception as e:
                    logger.error(f"❌ PGI service load failed: {e}")
                    self._loader_cache[ModuleType.PGI] = None
            return self._loader_cache[ModuleType.PGI]
    
    def _load_pod_service(self):
        """Load POD service."""
        with self._cache_lock:
            if ModuleType.POD not in self._loader_cache:
                try:
                    from app.services.pod_service import PODService
                    self._loader_cache[ModuleType.POD] = PODService()
                    logger.info("✅ POD service loaded")
                except Exception as e:
                    logger.error(f"❌ POD service load failed: {e}")
                    self._loader_cache[ModuleType.POD] = None
            return self._loader_cache[ModuleType.POD]
    
    def _load_logistics_service(self):
        """Load Logistics service."""
        with self._cache_lock:
            if ModuleType.LOGISTICS not in self._loader_cache:
                try:
                    from app.services.logistics_service import LogisticsService
                    self._loader_cache[ModuleType.LOGISTICS] = LogisticsService()
                    logger.info("✅ Logistics service loaded")
                except Exception as e:
                    logger.error(f"❌ Logistics service load failed: {e}")
                    self._loader_cache[ModuleType.LOGISTICS] = None
            return self._loader_cache[ModuleType.LOGISTICS]
    
    def _load_sales_office_service(self):
        """Load Sales Office service."""
        with self._cache_lock:
            if ModuleType.SALES_OFFICE not in self._loader_cache:
                try:
                    from app.services.sales_office_service import SalesOfficeService
                    self._loader_cache[ModuleType.SALES_OFFICE] = SalesOfficeService()
                    logger.info("✅ Sales Office service loaded")
                except Exception as e:
                    logger.error(f"❌ Sales Office service load failed: {e}")
                    self._loader_cache[ModuleType.SALES_OFFICE] = None
            return self._loader_cache[ModuleType.SALES_OFFICE]
    
    def _load_transport_service(self):
        """Load Transport service."""
        with self._cache_lock:
            if ModuleType.TRANSPORT not in self._loader_cache:
                try:
                    from app.services.transport_service import TransportService
                    self._loader_cache[ModuleType.TRANSPORT] = TransportService()
                    logger.info("✅ Transport service loaded")
                except Exception as e:
                    logger.error(f"❌ Transport service load failed: {e}")
                    self._loader_cache[ModuleType.TRANSPORT] = None
            return self._loader_cache[ModuleType.TRANSPORT]
    
    def _load_forecast_service(self):
        """Load Forecast service."""
        with self._cache_lock:
            if ModuleType.FORECAST not in self._loader_cache:
                try:
                    from app.services.forecast_service import ForecastService
                    self._loader_cache[ModuleType.FORECAST] = ForecastService()
                    logger.info("✅ Forecast service loaded")
                except Exception as e:
                    logger.error(f"❌ Forecast service load failed: {e}")
                    self._loader_cache[ModuleType.FORECAST] = None
            return self._loader_cache[ModuleType.FORECAST]
    
    def _load_reports_service(self):
        """Load Reports service."""
        with self._cache_lock:
            if ModuleType.REPORTS not in self._loader_cache:
                try:
                    from app.services.reports_service import ReportsService
                    self._loader_cache[ModuleType.REPORTS] = ReportsService()
                    logger.info("✅ Reports service loaded")
                except Exception as e:
                    logger.error(f"❌ Reports service load failed: {e}")
                    self._loader_cache[ModuleType.REPORTS] = None
            return self._loader_cache[ModuleType.REPORTS]
    
    def _load_management_service(self):
        """Load Management service."""
        with self._cache_lock:
            if ModuleType.MANAGEMENT not in self._loader_cache:
                try:
                    from app.services.management_service import ManagementService
                    self._loader_cache[ModuleType.MANAGEMENT] = ManagementService()
                    logger.info("✅ Management service loaded")
                except Exception as e:
                    logger.error(f"❌ Management service load failed: {e}")
                    self._loader_cache[ModuleType.MANAGEMENT] = None
            return self._loader_cache[ModuleType.MANAGEMENT]
    
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
        
        # Check each menu item
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
    2. Route to modules (GENERIC - no special cases)
    3. Forward messages when locked
    4. Show Main Dashboard
    5. Handle "__EXIT__" signal
    
    NO business logic.
    NO SQL queries.
    NO analytics.
    NO answering questions directly.
    NO special-case logic for any module.
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
        logger.info("🚀 ENTERPRISE GATEWAY v44.0 initialized")
        logger.info("   📦 SOLE entry point for all interactions")
        logger.info("   🔒 GENERIC session locking (all modules equal)")
        logger.info("   🔀 Routes to any registered module")
        logger.info("   🚫 NO special-case logic")
        logger.info("   🚫 NO business logic")
        logger.info("   🚫 NO SQL queries")
        logger.info("   🚫 NO analytics")
        logger.info("   📋 Shows Main Dashboard")
        logger.info("   🚪 Only '__EXIT__' unlocks")
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
                dashboard_name=menu_item.name,
                service_instance=service_instance
            )
            
            logger.info(f"🔒 Session LOCKED for {sender} → {menu_item.name} ({menu_item.file})")
            return True
    
    def _unlock_session(self, sender: str) -> bool:
        """Unlock session."""
        with self._session_lock:
            if sender not in self._sessions:
                return False
            
            session = self._sessions[sender]
            module_name = session.module_name
            session.unlock()
            
            logger.info(f"🔓 Session UNLOCKED for {sender} from {module_name}")
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
    # MODULE ROUTING - GENERIC
    # ============================================================
    
    def _detect_dashboard(self, message: str) -> Optional[tuple[MenuItem, Any]]:
        """
        Detect which dashboard the user wants - GENERIC.
        
        Supports:
        - Menu numbers: "1", "2", etc.
        - Dashboard names: "DN Dashboard", "Warehouse Dashboard"
        - Aliases: "dn", "warehouse", "pending dn"
        """
        return self._registry.get_service_by_text(message)
    
    def _forward_to_module(self, session: Session, message: str, sender: str) -> str:
        """Forward message to locked module - GENERIC."""
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
            result = service.process_whatsapp_query(message, sender)
            
            # Check for exit signal
            if result == EXIT_SIGNAL:
                logger.info(f"🚪 Module {session.module_name} requested exit ({EXIT_SIGNAL})")
                self._unlock_session(sender)
                return self._get_main_dashboard()
            
            # Update session activity
            session.update_activity()
            
            # Add to history
            session.add_history(message, result)
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Module {session.module_name} error: {e}")
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
        5. Handle commands → Show Main Dashboard
        6. Detect dashboard → Lock and Route
        7. No detection → Show Main Dashboard
        """
        sender = sender or sender_id or "default"
        
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
            
            # Lock session
            self._lock_session(sender, menu_item, service)
            
            try:
                # Forward to service
                result = service.process_whatsapp_query(message_clean, sender)
                
                # Check for immediate exit
                if result == EXIT_SIGNAL:
                    self._unlock_session(sender)
                    return self._get_main_dashboard()
                
                # Update session
                session = self._get_session(sender)
                session.update_activity()
                session.add_history(message_clean, result)
                
                return result
                
            except Exception as e:
                logger.error(f"❌ Module {menu_item.name} error: {e}")
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
            "• Type a number (1-{}) to enter a dashboard".format(len(self._registry.get_menu_items())),
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
            "• Number (1-{})".format(len(self._registry.get_menu_items())),
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
            "version": "44.0",
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
    "MenuItem",
    "ServiceRegistry",
    "get_ai_provider_service",
    "process_whatsapp_query",
    "EXIT_SIGNAL",
]
