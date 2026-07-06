#!/usr/bin/env python3
# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 2.1 - ENTERPRISE AI GATEWAY SERVICE (FULL)
# ============================================================

"""
================================================================================
AI PROVIDER SERVICE - ENTERPRISE GATEWAY
================================================================================

Central gateway service for the HPK Logistics AI WhatsApp Agent.
Routes queries to appropriate specialized services based on intent.

Architecture:
    WhatsApp → webhook.py → ai_provider_service.py → 
        ├── national_kpi_service.py
        ├── dn_analysis.py
        ├── dealer_analytics_service.py
        ├── warehouse_service.py
        ├── product_service.py
        ├── city_service.py
        └── groq_service.py

Features:
    ✅ Intent detection and routing
    ✅ Session management with context
    ✅ Multi-service orchestration
    ✅ Fallback to Groq AI
    ✅ Comprehensive logging
    ✅ Enterprise-grade performance
    ✅ Graceful service degradation
    ✅ All 7 services integrated
================================================================================
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# ============================================================
# SERVICE IMPORTS - ALL 7 SERVICES
# ============================================================

# 1. Dealer Analytics Service (Primary)
try:
    from app.services.dealer_analytics_service import get_dealer_service, EXIT_SIGNAL
    DEALER_AVAILABLE = True
    logger.info("✅ Dealer Analytics Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Dealer Analytics Service not available: {e}")
    get_dealer_service = None
    EXIT_SIGNAL = "__EXIT__"
    DEALER_AVAILABLE = False

# 2. National KPI Service
try:
    from app.services.national_kpi_service import get_kpi_service
    KPI_AVAILABLE = True
    logger.info("✅ National KPI Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ National KPI Service not available: {e}")
    get_kpi_service = None
    KPI_AVAILABLE = False

# 3. DN Analysis Service
try:
    from app.services.dn_analysis import get_dn_analysis_service
    DN_AVAILABLE = True
    logger.info("✅ DN Analysis Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ DN Analysis Service not available: {e}")
    get_dn_analysis_service = None
    DN_AVAILABLE = False

# 4. Warehouse Service
try:
    from app.services.warehouse_service import get_warehouse_service
    WAREHOUSE_AVAILABLE = True
    logger.info("✅ Warehouse Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Warehouse Service not available: {e}")
    get_warehouse_service = None
    WAREHOUSE_AVAILABLE = False

# 5. Product Service
try:
    from app.services.product_service import get_product_service
    PRODUCT_AVAILABLE = True
    logger.info("✅ Product Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Product Service not available: {e}")
    get_product_service = None
    PRODUCT_AVAILABLE = False

# 6. City Service
try:
    from app.services.city_service import get_city_service
    CITY_AVAILABLE = True
    logger.info("✅ City Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ City Service not available: {e}")
    get_city_service = None
    CITY_AVAILABLE = False

# 7. Groq AI Service (Fallback)
try:
    from app.services.groq_service import get_groq_service
    GROQ_AVAILABLE = True
    logger.info("✅ Groq AI Service loaded")
except ImportError as e:
    logger.warning(f"⚠️ Groq AI Service not available: {e}")
    get_groq_service = None
    GROQ_AVAILABLE = False

# ============================================================
# CONSTANTS
# ============================================================

VERSION = "2.1"
MAX_RESPONSE_LENGTH = 4096  # WhatsApp limit

# Intent patterns with priority scoring
INTENT_PATTERNS = {
    "exit": [
        (r'(?i)^\s*99\s*$', 10),
        (r'(?i)^\s*exit\s*$', 9),
        (r'(?i)^\s*quit\s*$', 9),
        (r'(?i)^\s*back\s*$', 8),
        (r'(?i)^\s*menu\s*$', 5),
    ],
    "help": [
        (r'(?i)^\s*help\s*$', 10),
        (r'(?i)^\s*\?\s*$', 9),
        (r'(?i)^\s*start\s*$', 8),
        (r'(?i)^\s*hello\s*$', 7),
        (r'(?i)^\s*hi\s*$', 7),
        (r'(?i)^\s*options\s*$', 8),
    ],
    "dealer": [
        (r'(?i)dealer', 5),
        (r'(?i)customer', 4),
        (r'(?i)distributor', 4),
        (r'(?i)arshad', 8),
        (r'(?i)zoom', 8),
        (r'(?i)ruba', 8),
        (r'(?i)metro', 8),
        (r'(?i)friends', 8),
        (r'(?i)electronics', 7),
        (r'(?i)appliances', 7),
        (r'(?i)digital', 7),
        (r'(?i)traders', 7),
        (r'(?i)galaxy', 8),
        (r'(?i)madina', 8),
        (r'(?i)star', 7),
        (r'(?i)enterprises', 7),
        (r'(?i)corporation', 7),
        (r'(?i)limited', 6),
        (r'(?i)ltd', 6),
        (r'(?i)pvt', 6),
        (r'(?i)private', 6),
    ],
    "kpi": [
        (r'(?i)^\s*kpi\s*$', 10),
        (r'(?i)^\s*kpis\s*$', 10),
        (r'(?i)performance', 7),
        (r'(?i)metrics', 7),
        (r'(?i)statistics', 6),
        (r'(?i)overall', 6),
        (r'(?i)national', 6),
        (r'(?i)company.?wide', 6),
        (r'(?i)total\s+(?:sales|delivery|performance)', 8),
        (r'(?i)dashboard', 5),
        (r'(?i)key\s+performance', 8),
    ],
    "dn": [
        (r'(?i)dn[:\s]*[A-Za-z0-9\-]+', 10),
        (r'(?i)delivery\s+note[:\s]*[A-Za-z0-9\-]+', 10),
        (r'(?i)track\s+dn', 9),
        (r'(?i)check\s+dn', 9),
        (r'(?i)dn\s+status', 8),
        (r'(?i)delivery\s+note\s+status', 8),
        (r'(?i)[A-Za-z]{2,4}[-]?\d{4,}', 7),  # Pattern like DN-12345
    ],
    "warehouse": [
        (r'(?i)warehouse', 5),
        (r'(?i)stock', 5),
        (r'(?i)inventory', 5),
        (r'(?i)godown', 5),
        (r'(?i)warehouse\s+performance', 8),
        (r'(?i)warehouse\s+metrics', 8),
        (r'(?i)stock\s+level', 7),
        (r'(?i)inventory\s+status', 7),
    ],
    "product": [
        (r'(?i)product', 5),
        (r'(?i)material', 5),
        (r'(?i)item', 5),
        (r'(?i)article', 5),
        (r'(?i)sales\s+by\s+product', 8),
        (r'(?i)product\s+performance', 8),
        (r'(?i)top\s+product', 7),
        (r'(?i)best\s+selling', 7),
        (r'(?i)product\s+analytics', 8),
    ],
    "city": [
        (r'(?i)city', 5),
        (r'(?i)region', 5),
        (r'(?i)area', 5),
        (r'(?i)location', 5),
        (r'(?i)sales\s+by\s+city', 8),
        (r'(?i)city\s+performance', 8),
        (r'(?i)regional\s+sales', 7),
        (r'(?i)city\s+analytics', 8),
    ]
}

# ============================================================
# SESSION MANAGEMENT
# ============================================================

class SessionData:
    """User session data with context"""
    def __init__(self):
        self.last_intent: str = ""
        self.last_query: str = ""
        self.last_response: str = ""
        self.context: Dict[str, Any] = {}
        self.created_at: datetime = datetime.now()
        self.updated_at: datetime = datetime.now()
        self.conversation_history: List[Dict[str, str]] = []
        self.pending_action: Optional[str] = None
        self.dealer_session: Any = None
        self.selected_dealer: Optional[str] = None
        self.search_results: List[Dict[str, Any]] = []
        self.current_page: int = 0
        
    def add_history(self, query: str, intent: str, response: str = ""):
        """Add to conversation history"""
        self.conversation_history.append({
            "query": query,
            "intent": intent,
            "timestamp": datetime.now().isoformat()
        })
        # Keep last 50 messages
        if len(self.conversation_history) > 50:
            self.conversation_history = self.conversation_history[-50:]
        self.updated_at = datetime.now()
    
    def get_context(self, key: str, default=None):
        """Get context value"""
        return self.context.get(key, default)
    
    def set_context(self, key: str, value: Any):
        """Set context value"""
        self.context[key] = value
        self.updated_at = datetime.now()

# ============================================================
# AI PROVIDER SERVICE
# ============================================================

class AIProviderService:
    """
    Central AI Provider Service - Routes queries to specialized services
    """
    
    _instance: Optional["AIProviderService"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        
        self._initialized = True
        self._version = VERSION
        self._sessions: Dict[str, SessionData] = {}
        self._total_requests = 0
        self._successful_requests = 0
        self._errors = 0
        self._startup = datetime.now()
        self._last_cleanup = datetime.now()
        
        # Service availability flags
        self._services_available = {
            "dealer": DEALER_AVAILABLE,
            "kpi": KPI_AVAILABLE,
            "dn": DN_AVAILABLE,
            "warehouse": WAREHOUSE_AVAILABLE,
            "product": PRODUCT_AVAILABLE,
            "city": CITY_AVAILABLE,
            "groq": GROQ_AVAILABLE
        }
        
        # Service instances cache
        self._service_instances = {}
        
        self._show_startup()
        
        # Cleanup stale sessions every hour
        self._cleanup_sessions()
    
    def _show_startup(self):
        """Display startup information"""
        print("\n" + "=" * 70)
        print(f"🤖 AI PROVIDER GATEWAY v{self._version}".center(70))
        print("=" * 70)
        print("📋 SERVICES AVAILABLE:")
        print("-" * 70)
        
        # Show all 7 services
        service_names = {
            "dealer": "Dealer Analytics",
            "kpi": "National KPI",
            "dn": "DN Analysis",
            "warehouse": "Warehouse",
            "product": "Product Analytics",
            "city": "City Analytics",
            "groq": "Groq AI (Fallback)"
        }
        
        for service_key, display_name in service_names.items():
            available = self._services_available.get(service_key, False)
            status = "✅" if available else "❌"
            name = display_name.ljust(20)
            print(f"  {status}  {name} : {'Available' if available else 'Not Available'}")
        
        print("-" * 70)
        print(f"  📊 Started at: {self._startup.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70 + "\n")
    
    # ============================================================
    # CORE PROCESSING
    # ============================================================
    
    async def process_whatsapp_query(self, message: str, sender: str) -> str:
        """
        Main entry point for WhatsApp queries.
        
        Args:
            message: User's message
            sender: Sender's phone number
            
        Returns:
            Response string
        """
        self._total_requests += 1
        start_time = datetime.now()
        
        try:
            logger.info(f"📨 Processing: '{message}' from {sender}")
            
            if not message or not message.strip():
                return self._get_welcome_message()
            
            msg = message.strip()
            
            # Get or create session
            session = self._get_session(sender)
            session.last_query = msg
            session.updated_at = datetime.now()
            
            # Check for exit commands (highest priority)
            if self._is_exit_command(msg):
                session.pending_action = None
                session.selected_dealer = None
                session.last_intent = ""
                logger.info(f"🚪 Exit command from {sender}")
                return self._get_welcome_message()
            
            # Check for help commands
            if self._is_help_command(msg):
                logger.info(f"💡 Help requested by {sender}")
                return self._get_help_message()
            
            # Detect intent and route
            intent, confidence = self._detect_intent(msg)
            logger.info(f"🎯 Intent detected: {intent} (confidence: {confidence:.2f})")
            
            # Route to appropriate service
            response = await self._route_to_service(msg, sender, session, intent)
            
            # Update session
            session.last_intent = intent
            session.last_response = response
            session.add_history(msg, intent, response[:100])
            
            # Truncate response if needed
            if len(response) > MAX_RESPONSE_LENGTH:
                response = response[:MAX_RESPONSE_LENGTH - 50] + "\n\n...(truncated)"
            
            self._successful_requests += 1
            elapsed = (datetime.now() - start_time).total_seconds() * 1000
            logger.info(f"✅ Response in {elapsed:.0f}ms - Intent: {intent}")
            
            return response
            
        except Exception as e:
            self._errors += 1
            logger.error(f"❌ Error processing query: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return self._get_error_message()
    
    # ============================================================
    # INTENT DETECTION (Enhanced)
    # ============================================================
    
    def _detect_intent(self, message: str) -> Tuple[str, float]:
        """
        Detect user intent with confidence scoring.
        
        Returns:
            Tuple of (intent, confidence_score)
        """
        msg = message.lower().strip()
        
        # If message is a dealer name (common case), boost dealer intent
        if len(msg) > 3 and not any(c in msg for c in [' ', '\t']):
            # Single word - could be dealer name
            if msg not in ['help', 'menu', 'kpi', 'dn', 'warehouse', 'product', 'city']:
                return "dealer", 0.7
        
        # Check each intent with priority scoring
        scores = {}
        
        for intent, patterns in INTENT_PATTERNS.items():
            max_score = 0
            for pattern, priority in patterns:
                if re.search(pattern, msg, re.IGNORECASE):
                    max_score = max(max_score, priority)
            if max_score > 0:
                scores[intent] = max_score
        
        # If no intent matched, check for DN pattern
        if not scores:
            dn_match = re.search(r'[A-Za-z]{2,4}[-]?\d{4,}', msg)
            if dn_match:
                return "dn", 0.6
        
        # Return best match with confidence
        if scores:
            best_intent = max(scores, key=scores.get)
            confidence = scores[best_intent] / 10.0
            return best_intent, confidence
        
        # Default to dealer (most common use case)
        return "dealer", 0.3
    
    def _is_exit_command(self, message: str) -> bool:
        """Check if message is an exit command"""
        msg = message.lower().strip()
        exit_patterns = [
            r'^\s*99\s*$',
            r'^\s*exit\s*$',
            r'^\s*quit\s*$',
            r'^\s*back\s*$',
            r'^\s*menu\s*$'
        ]
        for pattern in exit_patterns:
            if re.search(pattern, msg):
                return True
        return False
    
    def _is_help_command(self, message: str) -> bool:
        """Check if message is a help command"""
        msg = message.lower().strip()
        help_patterns = [
            r'^\s*help\s*$',
            r'^\s*\?\s*$',
            r'^\s*start\s*$',
            r'^\s*hello\s*$',
            r'^\s*hi\s*$',
            r'^\s*options\s*$'
        ]
        for pattern in help_patterns:
            if re.search(pattern, msg):
                return True
        return False
    
    # ============================================================
    # ROUTING ENGINE
    # ============================================================
    
    async def _route_to_service(self, message: str, sender: str, session: SessionData, intent: str) -> str:
        """
        Route to appropriate service based on intent.
        """
        
        # Route to specialized service
        if intent == "dealer":
            return self._handle_dealer_query(message, sender, session)
        
        elif intent == "kpi":
            return self._handle_kpi_query(message, session)
        
        elif intent == "dn":
            return self._handle_dn_query(message, session)
        
        elif intent == "warehouse":
            return self._handle_warehouse_query(message, session)
        
        elif intent == "product":
            return self._handle_product_query(message, session)
        
        elif intent == "city":
            return self._handle_city_query(message, session)
        
        else:
            # Try dealer as fallback
            if self._services_available["dealer"]:
                response = self._handle_dealer_query(message, sender, session)
                if response and "not found" not in response.lower():
                    return response
                elif self._services_available["groq"]:
                    return self._handle_ai_query(message, session)
                else:
                    return self._get_fallback_message()
            elif self._services_available["groq"]:
                return self._handle_ai_query(message, session)
            else:
                return self._get_fallback_message()
    
    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================
    
    def _get_session(self, user_id: str) -> SessionData:
        """Get or create user session"""
        if user_id not in self._sessions:
            self._sessions[user_id] = SessionData()
            logger.info(f"🆕 Session created for {user_id}")
        return self._sessions[user_id]
    
    def _cleanup_sessions(self):
        """Clean up stale sessions periodically"""
        import asyncio
        
        async def cleanup_loop():
            while True:
                await asyncio.sleep(3600)  # Every hour
                try:
                    now = datetime.now()
                    stale = []
                    for user_id, session in self._sessions.items():
                        # Remove sessions inactive for > 24 hours
                        if (now - session.updated_at).seconds > 86400:
                            stale.append(user_id)
                    
                    for user_id in stale:
                        del self._sessions[user_id]
                    
                    if stale:
                        logger.info(f"🧹 Cleaned up {len(stale)} stale sessions")
                except Exception as e:
                    logger.error(f"❌ Session cleanup error: {e}")
        
        # Start cleanup in background
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            loop.create_task(cleanup_loop())
        except RuntimeError:
            # If no event loop, schedule later
            import threading
            threading.Timer(3600, self._cleanup_sessions).start()
    
    # ============================================================
    # SERVICE HANDLERS - ALL 7 SERVICES
    # ============================================================
    
    def _handle_dealer_query(self, message: str, sender: str, session: SessionData) -> str:
        """Route to Dealer Analytics Service (Service 1)"""
        try:
            if not self._services_available["dealer"]:
                return self._get_service_unavailable_message("Dealer Analytics")
            
            # Get or create service instance
            if "dealer" not in self._service_instances:
                self._service_instances["dealer"] = get_dealer_service()
            
            service = self._service_instances["dealer"]
            if service:
                # Check if it's a selection (numeric)
                if message.isdigit() and session.search_results:
                    response = service.process_whatsapp_query(message, sender)
                else:
                    response = service.process_whatsapp_query(message, sender)
                
                if response == EXIT_SIGNAL:
                    session.last_intent = ""
                    return self._get_welcome_message()
                
                # Store search results for pagination if present
                if "suggestions" in response.lower():
                    # Try to extract search results from response
                    pass
                
                return response
            else:
                logger.warning("⚠️ Dealer service not available")
                return self._get_service_unavailable_message("Dealer Analytics")
        except Exception as e:
            logger.error(f"❌ Dealer service error: {e}")
            return self._get_error_message()
    
    def _handle_kpi_query(self, message: str, session: SessionData) -> str:
        """Route to National KPI Service (Service 2)"""
        try:
            if not self._services_available["kpi"]:
                return self._get_service_unavailable_message("National KPI")
            
            if "kpi" not in self._service_instances:
                self._service_instances["kpi"] = get_kpi_service()
            
            service = self._service_instances["kpi"]
            if service:
                if hasattr(service, 'process_query'):
                    return service.process_query(message)
                elif hasattr(service, 'get_kpi_dashboard'):
                    return service.get_kpi_dashboard()
                elif hasattr(service, 'get_dashboard'):
                    return service.get_dashboard()
                else:
                    return "📊 National KPI Dashboard\n\nPlease wait while we fetch the data..."
            else:
                return self._get_service_unavailable_message("National KPI")
        except Exception as e:
            logger.error(f"❌ KPI service error: {e}")
            return self._get_error_message()
    
    def _handle_dn_query(self, message: str, session: SessionData) -> str:
        """Route to DN Analysis Service (Service 3)"""
        try:
            if not self._services_available["dn"]:
                return self._get_service_unavailable_message("DN Analysis")
            
            if "dn" not in self._service_instances:
                self._service_instances["dn"] = get_dn_analysis_service()
            
            service = self._service_instances["dn"]
            if service:
                if hasattr(service, 'process_query'):
                    return service.process_query(message)
                elif hasattr(service, 'analyze_dn'):
                    # Extract DN number
                    dn_match = re.search(r'[A-Za-z0-9\-]{6,}', message)
                    if dn_match:
                        return service.analyze_dn(dn_match.group())
                    else:
                        return "📦 Please provide a valid Delivery Note number.\n\nExample: DN-12345"
                elif hasattr(service, 'track_dn'):
                    dn_match = re.search(r'[A-Za-z0-9\-]{6,}', message)
                    if dn_match:
                        return service.track_dn(dn_match.group())
                    else:
                        return "📦 Please provide a valid Delivery Note number.\n\nExample: DN-12345"
                else:
                    return "📦 DN Analysis\n\nPlease provide a Delivery Note number to track."
            else:
                return self._get_service_unavailable_message("DN Analysis")
        except Exception as e:
            logger.error(f"❌ DN service error: {e}")
            return self._get_error_message()
    
    def _handle_warehouse_query(self, message: str, session: SessionData) -> str:
        """Route to Warehouse Service (Service 4)"""
        try:
            if not self._services_available["warehouse"]:
                return self._get_service_unavailable_message("Warehouse")
            
            if "warehouse" not in self._service_instances:
                self._service_instances["warehouse"] = get_warehouse_service()
            
            service = self._service_instances["warehouse"]
            if service:
                if hasattr(service, 'process_query'):
                    return service.process_query(message)
                elif hasattr(service, 'get_warehouse_dashboard'):
                    return service.get_warehouse_dashboard()
                elif hasattr(service, 'get_dashboard'):
                    return service.get_dashboard()
                else:
                    return "🏭 Warehouse Intelligence\n\nPlease wait while we fetch the data..."
            else:
                return self._get_service_unavailable_message("Warehouse")
        except Exception as e:
            logger.error(f"❌ Warehouse service error: {e}")
            return self._get_error_message()
    
    def _handle_product_query(self, message: str, session: SessionData) -> str:
        """Route to Product Service (Service 5)"""
        try:
            if not self._services_available["product"]:
                return self._get_service_unavailable_message("Product Analytics")
            
            if "product" not in self._service_instances:
                self._service_instances["product"] = get_product_service()
            
            service = self._service_instances["product"]
            if service:
                if hasattr(service, 'process_query'):
                    return service.process_query(message)
                elif hasattr(service, 'get_product_analytics'):
                    return service.get_product_analytics()
                elif hasattr(service, 'get_analytics'):
                    return service.get_analytics()
                else:
                    return "📦 Product Analytics\n\nPlease wait while we fetch the data..."
            else:
                return self._get_service_unavailable_message("Product Analytics")
        except Exception as e:
            logger.error(f"❌ Product service error: {e}")
            return self._get_error_message()
    
    def _handle_city_query(self, message: str, session: SessionData) -> str:
        """Route to City Service (Service 6)"""
        try:
            if not self._services_available["city"]:
                return self._get_service_unavailable_message("City Analytics")
            
            if "city" not in self._service_instances:
                self._service_instances["city"] = get_city_service()
            
            service = self._service_instances["city"]
            if service:
                if hasattr(service, 'process_query'):
                    return service.process_query(message)
                elif hasattr(service, 'get_city_analytics'):
                    return service.get_city_analytics()
                elif hasattr(service, 'get_analytics'):
                    return service.get_analytics()
                else:
                    return "📍 City Analytics\n\nPlease wait while we fetch the data..."
            else:
                return self._get_service_unavailable_message("City Analytics")
        except Exception as e:
            logger.error(f"❌ City service error: {e}")
            return self._get_error_message()
    
    def _handle_ai_query(self, message: str, session: SessionData) -> str:
        """Route to Groq AI Service (Service 7 - Fallback)"""
        try:
            if not self._services_available["groq"]:
                return self._get_service_unavailable_message("AI Assistant")
            
            if "groq" not in self._service_instances:
                self._service_instances["groq"] = get_groq_service()
            
            service = self._service_instances["groq"]
            if service:
                if hasattr(service, 'process_query'):
                    return service.process_query(message)
                elif hasattr(service, 'generate_response'):
                    return service.generate_response(message)
                elif hasattr(service, 'chat'):
                    return service.chat(message)
                else:
                    return "🤖 AI Assistant\n\nHow can I help you today?"
            else:
                return self._get_service_unavailable_message("AI Assistant")
        except Exception as e:
            logger.error(f"❌ AI service error: {e}")
            return self._get_error_message()
    
    # ============================================================
    # MESSAGE GENERATORS
    # ============================================================
    
    def _get_welcome_message(self) -> str:
        """Get welcome message"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🚚 HPK LOGISTICS AI ASSISTANT",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Welcome! What would you like to check?",
            "",
            "📊 Available Services:",
            "",
            "👤 Dealer Intelligence",
            "   → Search dealer performance",
            "   → Check delivery metrics",
            "",
            "📊 National KPI Dashboard",
            "   → Company-wide performance",
            "   → Key metrics overview",
            "",
            "📦 Delivery Note Analysis",
            "   → Track specific DNs",
            "   → Check delivery status",
            "",
            "🏭 Warehouse Intelligence",
            "   → Warehouse performance",
            "   → Stock and inventory",
            "",
            "📦 Product Analytics",
            "   → Product performance",
            "   → Sales by product",
            "",
            "📍 City Analytics",
            "   → Sales by city",
            "   → Regional performance",
            "",
            "💡 Try: Dealer Name, DN Number, KPI, etc.",
            "📝 Type 'help' for detailed commands",
            "🔙 Type '99' to exit",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _get_help_message(self) -> str:
        """Get detailed help message"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "💡 HELP & AVAILABLE COMMANDS",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "🔍 DEALER SEARCH:",
            "   • Dealer name (e.g., Arshad Electronics)",
            "   • Dealer code (e.g., DLR-045)",
            "   • Customer code (e.g., CUST-789)",
            "",
            "📊 KPI DASHBOARD:",
            "   • Type 'KPI', 'performance', 'metrics'",
            "",
            "📦 DN ANALYSIS:",
            "   • Type 'DN-12345' or 'track DN'",
            "",
            "🏭 WAREHOUSE:",
            "   • Type 'warehouse', 'stock', 'inventory'",
            "",
            "📦 PRODUCT:",
            "   • Type 'product', 'material', 'sales by product'",
            "",
            "📍 CITY:",
            "   • Type 'city', 'region', 'sales by city'",
            "",
            "💬 GENERAL:",
            "   • 'help' - Show this menu",
            "   • '99' or 'exit' - Return to main menu",
            "",
            "📝 Examples:",
            "   • Arshad Electronics-Khi",
            "   • Zoom Appliances",
            "   • DN-2025-001",
            "   • KPI",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _get_fallback_message(self) -> str:
        """Get fallback message"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "ℹ️ I couldn't find what you're looking for.",
            "",
            "Try these examples:",
            "• Arshad Electronics-Khi (Dealer)",
            "• KPI (Dashboard)",
            "• DN-12345 (Track DN)",
            "• warehouse (Warehouse)",
            "• product (Products)",
            "• city (Cities)",
            "",
            "Type 'help' for more options.",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _get_error_message(self) -> str:
        """Get error message"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "⚠️ SERVICE ERROR",
            "",
            "I encountered an error processing your request.",
            "",
            "Please try again or type 'help' for assistance.",
            "Type '99' to return to the main menu.",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    def _get_service_unavailable_message(self, service_name: str) -> str:
        """Get service unavailable message"""
        return "\n".join([
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"⚠️ {service_name} SERVICE UNAVAILABLE",
            "",
            "This service is currently not available.",
            "",
            "Please try another service or try again later.",
            "",
            "Type 'help' for available options.",
            "Type '99' to return to main menu.",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ])
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for the gateway"""
        uptime = (datetime.now() - self._startup).seconds
        
        # Get service health if available
        service_health = {}
        for service_name, available in self._services_available.items():
            if available and service_name in self._service_instances:
                try:
                    service = self._service_instances[service_name]
                    if hasattr(service, 'health_check'):
                        service_health[service_name] = service.health_check()
                    else:
                        service_health[service_name] = {"status": "available"}
                except Exception as e:
                    service_health[service_name] = {"status": "error", "error": str(e)}
            else:
                service_health[service_name] = {"status": "not_available"}
        
        return {
            "status": "healthy",
            "version": self._version,
            "uptime_seconds": uptime,
            "uptime_display": f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s",
            "total_requests": self._total_requests,
            "successful_requests": self._successful_requests,
            "errors": self._errors,
            "success_rate": round((self._successful_requests / max(self._total_requests, 1)) * 100, 1),
            "active_sessions": len(self._sessions),
            "services_available": self._services_available,
            "service_health": service_health,
            "started_at": self._startup.isoformat()
        }

# ============================================================
# SINGLETON AND EXPORTS
# ============================================================

_service_instance: Optional[AIProviderService] = None

def get_ai_provider_service() -> AIProviderService:
    """Get singleton instance of AIProviderService"""
    global _service_instance
    if _service_instance is None:
        _service_instance = AIProviderService()
    return _service_instance

# Async wrapper for compatibility with webhook
async def process_whatsapp_query(message: str, sender: str) -> str:
    """
    Async wrapper for WhatsApp query processing.
    
    This function is used by the webhook handler.
    
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
    "VERSION"
]

# ============================================================
# TEST MODE
# ============================================================

if __name__ == "__main__":
    import asyncio
    
    print("\n" + "=" * 70)
    print(f"🤖 AI PROVIDER GATEWAY v{VERSION} - TEST MODE".center(70))
    print("=" * 70)
    print()
    
    service = get_ai_provider_service()
    
    # Health check
    health = service.health_check()
    print("📊 HEALTH CHECK:")
    print("-" * 40)
    for key, value in health.items():
        if key not in ['service_health', 'services_available']:
            print(f"  {key}: {value}")
    print("-" * 40)
    print()
    
    # Interactive test
    async def test_loop():
        print("🔍 Enter a query (or '99' to exit)")
        print("=" * 70)
        print()
        
        while True:
            try:
                query = input("👤 You: ").strip()
                
                if query.lower() in ['99', 'exit', 'quit']:
                    print("\n👋 Goodbye!")
                    break
                
                if not query:
                    continue
                
                print("\n⏳ Processing...\n")
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
