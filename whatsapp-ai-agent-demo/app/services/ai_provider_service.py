"""
File: app/services/ai_provider_service.py
Version: 8.5 - ENTERPRISE FIXED: Service Loading with Circuit Breaker & Retry
Purpose: SINGLE ENTRY POINT for all WhatsApp requests.
"""

import logging
import os
import threading
import time
import importlib
import inspect
import re
import sys
import asyncio
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from functools import wraps

logger = logging.getLogger(__name__)

# ============================================================
# TENACITY FOR RETRY LOGIC (Add to requirements.txt)
# ============================================================

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False
    logger.warning("⚠️ Tenacity not installed. Install with: pip install tenacity>=8.5.0")

# ============================================================
# IMPORTS WITH FALLBACK
# ============================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    logger.info("✅ Core imports successful")
except ImportError as e:
    logger.error(f"❌ Core import failed: {e}")
    SessionLocal = None
    DeliveryReport = None

# ============================================================
# CIRCUIT BREAKER PATTERN
# ============================================================

class CircuitBreaker:
    """Circuit breaker pattern for service availability"""
    
    def __init__(self, service_name: str, failure_threshold: int = 3, timeout_seconds: int = 300):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self._lock = threading.RLock()
    
    def is_available(self) -> bool:
        with self._lock:
            if self.state == "CLOSED":
                return True
            
            if self.state == "OPEN":
                # Check if timeout has passed
                if self.last_failure_time:
                    elapsed = (datetime.now() - self.last_failure_time).total_seconds()
                    if elapsed >= self.timeout_seconds:
                        self.state = "HALF_OPEN"
                        self.failure_count = 0
                        logger.info(f"🔓 Circuit breaker {self.service_name} moved to HALF_OPEN")
                        return True
                return False
            
            if self.state == "HALF_OPEN":
                return True
            
            return False
    
    def record_success(self):
        with self._lock:
            self.failure_count = 0
            if self.state == "HALF_OPEN":
                self.state = "CLOSED"
                logger.info(f"✅ Circuit breaker {self.service_name} CLOSED (recovered)")
    
    def record_failure(self):
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()
            
            if self.state == "HALF_OPEN":
                self.state = "OPEN"
                logger.error(f"🔴 Circuit breaker {self.service_name} OPEN (half-open test failed)")
            elif self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
                logger.error(f"🔴 Circuit breaker {self.service_name} OPEN (threshold reached: {self.failure_count})")

# ============================================================
# SERVICE STATUS
# ============================================================

class ServiceStatus:
    READY = "READY"
    IN_DEVELOPMENT = "IN_DEVELOPMENT"
    NOT_STARTED = "NOT_STARTED"
    ERROR = "ERROR"
    DISABLED = "DISABLED"

# ============================================================
# SERVICE LOADER WITH MULTIPLE FALLBACKS & CIRCUIT BREAKER
# ============================================================

class ServiceLoader:
    """Load services with multiple fallback paths and circuit breakers"""
    
    _dealer_service = None
    _dn_service = None
    _groq_service = None
    _loaded = False
    _lock = threading.RLock()
    
    # Circuit breakers for each service
    _circuit_breakers = {
        'dealer': CircuitBreaker('dealer', failure_threshold=3, timeout_seconds=300),
        'dn': CircuitBreaker('dn', failure_threshold=3, timeout_seconds=300),
        'groq': CircuitBreaker('groq', failure_threshold=2, timeout_seconds=180)
    }
    
    @classmethod
    def load_all_services(cls):
        """Load all services with fallback paths and circuit breakers"""
        if cls._loaded:
            return
        
        with cls._lock:
            if cls._loaded:
                return
            
            logger.info("=" * 70)
            logger.info("🔄 Loading Services with Circuit Breakers...")
            logger.info("=" * 70)
            
            # Load Dealer Service
            cls._dealer_service = cls._load_service_with_retry(
                'dealer',
                cls._load_dealer_service
            )
            
            # Load DN Service
            cls._dn_service = cls._load_service_with_retry(
                'dn',
                cls._load_dn_service
            )
            
            # Load Groq Service
            cls._groq_service = cls._load_service_with_retry(
                'groq',
                cls._load_groq_service
            )
            
            cls._loaded = True
            
            # Print status
            logger.info("")
            logger.info("   SERVICE STATUS:")
            logger.info(f"   Dealer Service: {'✅' if cls._dealer_service else '❌'} Loaded")
            logger.info(f"   DN Service: {'✅' if cls._dn_service else '❌'} Loaded")
            logger.info(f"   Groq Service: {'✅' if cls._groq_service else '⚠️'} Available")
            logger.info("")
    
    @classmethod
    def _load_service_with_retry(cls, service_name: str, loader_func):
        """Load a service with retry logic"""
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"🔧 Loading {service_name} service (attempt {attempt}/{max_attempts})...")
                service = loader_func()
                if service:
                    cls._circuit_breakers[service_name].record_success()
                    logger.info(f"✅ {service_name} service loaded successfully")
                    return service
                else:
                    logger.warning(f"⚠️ {service_name} service returned None (attempt {attempt})")
                    if attempt < max_attempts:
                        time.sleep(2 ** attempt)  # Exponential backoff
            except Exception as e:
                logger.error(f"❌ {service_name} service load failed (attempt {attempt}): {e}")
                cls._circuit_breakers[service_name].record_failure()
                if attempt < max_attempts:
                    time.sleep(2 ** attempt)
        
        logger.error(f"❌ Failed to load {service_name} service after {max_attempts} attempts")
        return None
    
    @classmethod
    def _load_dealer_service(cls):
        """Load dealer service with multiple fallbacks"""
        
        # Try path 1: Standard import
        try:
            from app.services.dealer_analytics_service import get_dealer_analytics_service
            service = get_dealer_analytics_service()
            if service:
                logger.info("✅ Dealer Service loaded (standard path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Standard import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Standard import error: {e}")
        
        # Try path 2: Relative import
        try:
            from .dealer_analytics_service import get_dealer_analytics_service
            service = get_dealer_analytics_service()
            if service:
                logger.info("✅ Dealer Service loaded (relative path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Relative import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Relative import error: {e}")
        
        # Try path 3: Direct file import
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            if current_dir not in sys.path:
                sys.path.insert(0, current_dir)
            
            from dealer_analytics_service import get_dealer_analytics_service
            service = get_dealer_analytics_service()
            if service:
                logger.info("✅ Dealer Service loaded (direct path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Direct import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Direct import error: {e}")
        
        # Try path 4: Import from parent directory
        try:
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            from services.dealer_analytics_service import get_dealer_analytics_service
            service = get_dealer_analytics_service()
            if service:
                logger.info("✅ Dealer Service loaded (parent path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Parent import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Parent import error: {e}")
        
        # Try path 5: Dynamic module loading
        try:
            import importlib.util
            service_path = os.path.join(os.path.dirname(__file__), 'dealer_analytics_service.py')
            if os.path.exists(service_path):
                spec = importlib.util.spec_from_file_location("dealer_analytics_service", service_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, 'get_dealer_analytics_service'):
                    service = module.get_dealer_analytics_service()
                    if service:
                        logger.info("✅ Dealer Service loaded (dynamic path)")
                        return service
        except Exception as e:
            logger.warning(f"⚠️ Dynamic import failed: {e}")
        
        logger.error("❌ Failed to load Dealer Service after all fallbacks")
        return None
    
    @classmethod
    def _load_dn_service(cls):
        """Load DN service with multiple fallbacks"""
        
        # Try path 1: Standard import
        try:
            from app.services.dn_analysis import get_dn_analytics_service
            service = get_dn_analytics_service()
            if service:
                logger.info("✅ DN Service loaded (standard path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Standard import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Standard import error: {e}")
        
        # Try path 2: Relative import
        try:
            from .dn_analysis import get_dn_analytics_service
            service = get_dn_analytics_service()
            if service:
                logger.info("✅ DN Service loaded (relative path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Relative import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Relative import error: {e}")
        
        # Try path 3: Direct file import
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            if current_dir not in sys.path:
                sys.path.insert(0, current_dir)
            
            from dn_analysis import get_dn_analytics_service
            service = get_dn_analytics_service()
            if service:
                logger.info("✅ DN Service loaded (direct path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Direct import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Direct import error: {e}")
        
        # Try path 4: Import from parent directory
        try:
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            from services.dn_analysis import get_dn_analytics_service
            service = get_dn_analytics_service()
            if service:
                logger.info("✅ DN Service loaded (parent path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Parent import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Parent import error: {e}")
        
        # Try path 5: Dynamic module loading
        try:
            import importlib.util
            service_path = os.path.join(os.path.dirname(__file__), 'dn_analysis.py')
            if os.path.exists(service_path):
                spec = importlib.util.spec_from_file_location("dn_analysis", service_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, 'get_dn_analytics_service'):
                    service = module.get_dn_analytics_service()
                    if service:
                        logger.info("✅ DN Service loaded (dynamic path)")
                        return service
        except Exception as e:
            logger.warning(f"⚠️ Dynamic import failed: {e}")
        
        logger.error("❌ Failed to load DN Service after all fallbacks")
        return None
    
    @classmethod
    def _load_groq_service(cls):
        """Load Groq service with multiple fallbacks"""
        
        # Try path 1: Standard import
        try:
            from app.services.groq_service import get_groq_service
            service = get_groq_service()
            if service:
                logger.info("✅ Groq Service loaded (standard path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Groq import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Groq init failed: {e}")
        
        # Try path 2: Relative import
        try:
            from .groq_service import get_groq_service
            service = get_groq_service()
            if service:
                logger.info("✅ Groq Service loaded (relative path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Groq relative import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Groq relative init failed: {e}")
        
        # Try path 3: Direct file import
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            if current_dir not in sys.path:
                sys.path.insert(0, current_dir)
            
            from groq_service import get_groq_service
            service = get_groq_service()
            if service:
                logger.info("✅ Groq Service loaded (direct path)")
                return service
        except ImportError as e:
            logger.warning(f"⚠️ Groq direct import failed: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Groq direct init failed: {e}")
        
        # Return None (Groq is optional)
        logger.warning("⚠️ Groq service not available (optional)")
        return None
    
    @classmethod
    def get_dealer_service(cls):
        """Get dealer service instance with circuit breaker"""
        if not cls._loaded:
            cls.load_all_services()
        
        if not cls._circuit_breakers['dealer'].is_available():
            logger.warning("⚠️ Dealer service circuit breaker is OPEN")
            return None
        
        return cls._dealer_service
    
    @classmethod
    def get_dn_service(cls):
        """Get DN service instance with circuit breaker"""
        if not cls._loaded:
            cls.load_all_services()
        
        if not cls._circuit_breakers['dn'].is_available():
            logger.warning("⚠️ DN service circuit breaker is OPEN")
            return None
        
        return cls._dn_service
    
    @classmethod
    def get_groq_service(cls):
        """Get Groq service instance with circuit breaker"""
        if not cls._loaded:
            cls.load_all_services()
        
        if not cls._circuit_breakers['groq'].is_available():
            logger.warning("⚠️ Groq service circuit breaker is OPEN")
            return None
        
        return cls._groq_service
    
    @classmethod
    def get_service_status(cls) -> Dict[str, Any]:
        """Get detailed service status"""
        return {
            'dealer': {
                'loaded': cls._dealer_service is not None,
                'circuit_breaker': {
                    'state': cls._circuit_breakers['dealer'].state,
                    'failure_count': cls._circuit_breakers['dealer'].failure_count,
                    'last_failure': cls._circuit_breakers['dealer'].last_failure_time
                }
            },
            'dn': {
                'loaded': cls._dn_service is not None,
                'circuit_breaker': {
                    'state': cls._circuit_breakers['dn'].state,
                    'failure_count': cls._circuit_breakers['dn'].failure_count,
                    'last_failure': cls._circuit_breakers['dn'].last_failure_time
                }
            },
            'groq': {
                'loaded': cls._groq_service is not None,
                'circuit_breaker': {
                    'state': cls._circuit_breakers['groq'].state,
                    'failure_count': cls._circuit_breakers['groq'].failure_count,
                    'last_failure': cls._circuit_breakers['groq'].last_failure_time
                }
            }
        }

# ============================================================
# DEALER RESOLVER (IMPROVED)
# ============================================================

class DealerResolver:
    """Resolve dealer names from database with caching"""
    
    _dealer_cache = {}
    _dealer_names = []
    _loaded = False
    _lock = threading.RLock()
    _last_load_time = None
    _cache_ttl = 3600  # 1 hour
    
    @classmethod
    def load_dealers(cls, force_reload: bool = False):
        """Load dealers from database with caching"""
        if cls._loaded and not force_reload:
            # Check cache TTL
            if cls._last_load_time:
                elapsed = (datetime.now() - cls._last_load_time).total_seconds()
                if elapsed < cls._cache_ttl:
                    return
        
        with cls._lock:
            if cls._loaded and not force_reload:
                if cls._last_load_time:
                    elapsed = (datetime.now() - cls._last_load_time).total_seconds()
                    if elapsed < cls._cache_ttl:
                        return
            
            try:
                if not SessionLocal or not DeliveryReport:
                    logger.warning("⚠️ SessionLocal or DeliveryReport not available")
                    return
                
                session = SessionLocal()
                try:
                    # Check if table exists
                    from sqlalchemy import inspect
                    inspector = inspect(session.bind)
                    if not inspector.has_table(DeliveryReport.__tablename__):
                        logger.warning(f"⚠️ Table {DeliveryReport.__tablename__} does not exist")
                        return
                    
                    dealers = session.query(
                        DeliveryReport.customer_name,
                        DeliveryReport.dealer_code,
                        DeliveryReport.customer_code
                    ).filter(
                        DeliveryReport.customer_name.isnot(None)
                    ).distinct().all()
                    
                    cls._dealer_names = [
                        {
                            "name": d.customer_name,
                            "code": d.dealer_code or "",
                            "customer_code": d.customer_code or "",
                            "normalized": cls._normalize(d.customer_name)
                        }
                        for d in dealers if d.customer_name
                    ]
                    
                    cls._loaded = True
                    cls._last_load_time = datetime.now()
                    logger.info(f"✅ Loaded {len(cls._dealer_names)} dealers")
                except Exception as e:
                    logger.warning(f"❌ Failed to load dealers: {e}")
                finally:
                    session.close()
            except Exception as e:
                logger.warning(f"❌ Failed to load dealers: {e}")
    
    @staticmethod
    def _normalize(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip().lower()
    
    @classmethod
    def find_dealer(cls, dealer_name: str) -> Optional[str]:
        """Find dealer by name with improved matching"""
        if not dealer_name:
            return None
        
        cls.load_dealers()
        if not cls._dealer_names:
            return None
        
        normalized = cls._normalize(dealer_name)
        
        # Exact match
        for dealer in cls._dealer_names:
            if dealer["normalized"] == normalized:
                return dealer["name"]
        
        # Contains match
        for dealer in cls._dealer_names:
            if normalized in dealer["normalized"] or dealer["normalized"] in normalized:
                return dealer["name"]
        
        # Word match
        words = normalized.split()
        for word in words:
            if len(word) > 2:
                for dealer in cls._dealer_names:
                    if word in dealer["normalized"]:
                        return dealer["name"]
        
        # Fuzzy match (if python-Levenshtein is available)
        try:
            import Levenshtein
            best_match = None
            best_ratio = 0.7  # Threshold
            
            for dealer in cls._dealer_names:
                ratio = Levenshtein.ratio(normalized, dealer["normalized"])
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = dealer["name"]
            
            if best_match:
                return best_match
        except ImportError:
            pass
        
        return None
    
    @classmethod
    def find_similar(cls, dealer_name: str, limit: int = 5) -> List[str]:
        """Find similar dealers"""
        cls.load_dealers()
        if not cls._dealer_names:
            return []
        
        normalized = cls._normalize(dealer_name)
        results = []
        
        # Exact and contains matches first
        for dealer in cls._dealer_names:
            if normalized == dealer["normalized"] or \
               normalized in dealer["normalized"] or \
               dealer["normalized"] in normalized:
                if dealer["name"] not in results:
                    results.append(dealer["name"])
        
        # Word matches
        words = normalized.split()
        for dealer in cls._dealer_names:
            if dealer["name"] in results:
                continue
            if any(word in dealer["normalized"] for word in words if len(word) > 2):
                results.append(dealer["name"])
        
        # Fuzzy matches
        if len(results) < limit:
            try:
                import Levenshtein
                scored = []
                for dealer in cls._dealer_names:
                    if dealer["name"] in results:
                        continue
                    ratio = Levenshtein.ratio(normalized, dealer["normalized"])
                    scored.append((dealer["name"], ratio))
                
                scored.sort(key=lambda x: x[1], reverse=True)
                for name, ratio in scored[:limit - len(results)]:
                    if ratio > 0.6:
                        results.append(name)
            except ImportError:
                pass
        
        return results[:limit]
    
    @classmethod
    def get_all_dealers(cls) -> List[str]:
        """Get all dealer names"""
        cls.load_dealers()
        return [d["name"] for d in cls._dealer_names]

# ============================================================
# REST OF YOUR CODE (INTENT DETECTION, WHATSAPP PROVIDER, ETC.)
# ============================================================

# ... [Keep your existing IntentDetectionEngine and WhatsAppProviderService classes]
# ... [But update the service access methods to use circuit breakers]

# Here's the KEY FIX for WhatsAppProviderService:

class WhatsAppProviderService:
    def __init__(self):
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("AI Provider Service v8.5 - ENTERPRISE FIXED")
            logger.info("=" * 70)
            
            # Load all services with circuit breakers
            ServiceLoader.load_all_services()
            
            # Initialize intent engine
            self.intent_engine = IntentDetectionEngine()
            logger.info("✅ IntentDetectionEngine initialized")
            
            # Get service references
            self.dealer_service = ServiceLoader.get_dealer_service()
            self.dn_service = ServiceLoader.get_dn_service()
            self.groq_service = ServiceLoader.get_groq_service()
            
            # Pre-load dealers
            threading.Thread(target=DealerResolver.load_dealers, daemon=True).start()
            
            init_duration = (time.time() - start_time) * 1000
            logger.info(f"   INIT TIME: {init_duration:.2f}ms")
            logger.info("   STATUS: ✅ PRODUCTION GRADE WITH CIRCUIT BREAKERS")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.exception(f"❌ Failed to initialize: {str(e)}")
            raise
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender_id: Optional[str] = None
    ) -> Dict[str, Any]:
        logger.info(f"📩 Processing: '{message[:100]}'")
        start_time = time.perf_counter()
        
        try:
            routing_decision = self.intent_engine.detect_intent(message)
            logger.info(f"🎯 Intent: {routing_decision.intent}, Service: {routing_decision.service_key}")
            
            # DN Lookup
            if routing_decision.intent == "dn_lookup":
                return await self._handle_dn_with_retry(routing_decision)
            
            # Pending Queries
            if routing_decision.intent in ["pending_dn", "pending_pgi", "pending_pod"]:
                return await self._handle_pending_with_retry(routing_decision)
            
            # Dealer Suggestions
            if routing_decision.intent == "dealer_suggestion":
                return self._format_dealer_suggestions(routing_decision)
            
            # Dealer Dashboard
            if routing_decision.intent in ["dealer_dashboard", "dealer_profile"]:
                return await self._handle_dealer_with_retry(routing_decision)
            
            # Groq
            if routing_decision.needs_groq or routing_decision.service_key == "groq":
                return await self._handle_groq_with_retry(message, routing_decision)
            
            # Try dealer fallback
            if routing_decision.service_key == "dealer":
                return await self._handle_dealer_with_retry(routing_decision)
            
            # Try DN fallback
            if routing_decision.service_key == "dn":
                if routing_decision.intent == "dn_lookup":
                    return await self._handle_dn_with_retry(routing_decision)
                else:
                    return await self._handle_pending_with_retry(routing_decision)
            
            # Default
            return self._format_response(
                message,
                "I couldn't identify your request. Please specify:\n"
                "• A DN number (8-12 digits)\n"
                "• A dealer name (e.g., 'Taj Electronics')\n"
                "• A warehouse name\n"
                "• A city name\n"
                "• An analytics query (e.g., 'Top dealers')\n\n"
                "Type 'Help' for all commands.",
                error=False
            )
            
        except Exception as e:
            logger.exception(f"❌ Failed: {e}")
            return self._format_response(
                message,
                "⚠️ An unexpected error occurred. Please try again.",
                error=True
            )
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.info(f"⏱️ Response time: {elapsed_ms:.2f}ms")
    
    # ============================================================
    # NEW: HANDLERS WITH RETRY LOGIC
    # ============================================================
    
    async def _handle_dealer_with_retry(self, decision: RoutingDecision, max_retries: int = 2) -> Dict[str, Any]:
        """Handle dealer requests with retry logic"""
        for attempt in range(max_retries + 1):
            try:
                # Refresh service if needed
                if attempt > 0:
                    logger.info(f"🔄 Retry dealer attempt {attempt}/{max_retries}")
                    self.dealer_service = ServiceLoader.get_dealer_service()
                    if not self.dealer_service:
                        await asyncio.sleep(1)
                        continue
                
                result = await self._handle_dealer(decision)
                
                # Check if result indicates failure
                if isinstance(result, dict) and result.get("error"):
                    if attempt < max_retries:
                        logger.warning(f"⚠️ Dealer service returned error, retrying...")
                        await asyncio.sleep(1)
                        continue
                
                return result
                
            except Exception as e:
                logger.error(f"❌ Dealer handler attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
                raise
        
        return self._format_response(
            decision.original_message,
            "⚠️ Dealer service is temporarily unavailable. Please try again later.",
            error=True
        )
    
    async def _handle_dn_with_retry(self, decision: RoutingDecision, max_retries: int = 2) -> Dict[str, Any]:
        """Handle DN requests with retry logic"""
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"🔄 Retry DN attempt {attempt}/{max_retries}")
                    self.dn_service = ServiceLoader.get_dn_service()
                    if not self.dn_service:
                        await asyncio.sleep(1)
                        continue
                
                result = await self._handle_dn(decision)
                
                if isinstance(result, dict) and result.get("error"):
                    if attempt < max_retries:
                        logger.warning(f"⚠️ DN service returned error, retrying...")
                        await asyncio.sleep(1)
                        continue
                
                return result
                
            except Exception as e:
                logger.error(f"❌ DN handler attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
                raise
        
        return self._format_response(
            decision.original_message,
            "⚠️ DN service is temporarily unavailable. Please try again later.",
            error=True
        )
    
    async def _handle_pending_with_retry(self, decision: RoutingDecision, max_retries: int = 2) -> Dict[str, Any]:
        """Handle pending requests with retry logic"""
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"🔄 Retry pending attempt {attempt}/{max_retries}")
                    self.dn_service = ServiceLoader.get_dn_service()
                    if not self.dn_service:
                        await asyncio.sleep(1)
                        continue
                
                result = await self._handle_pending(decision)
                
                if isinstance(result, dict) and result.get("error"):
                    if attempt < max_retries:
                        logger.warning(f"⚠️ Pending service returned error, retrying...")
                        await asyncio.sleep(1)
                        continue
                
                return result
                
            except Exception as e:
                logger.error(f"❌ Pending handler attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                    continue
                raise
        
        return self._format_response(
            decision.original_message,
            "⚠️ Pending query service is temporarily unavailable. Please try again later.",
            error=True
        )
    
    async def _handle_groq_with_retry(self, message: str, decision: RoutingDecision, max_retries: int = 2) -> Dict[str, Any]:
        """Handle Groq requests with retry logic"""
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"🔄 Retry Groq attempt {attempt}/{max_retries}")
                    self.groq_service = ServiceLoader.get_groq_service()
                    if not self.groq_service:
                        await asyncio.sleep(0.5)
                        continue
                
                result = await self._handle_groq(message, decision)
                return result
                
            except Exception as e:
                logger.error(f"❌ Groq handler attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(0.5)
                    continue
                raise
        
        # Final fallback for Groq
        return self._format_response(
            message,
            "I'm here to help! What would you like to know about Sham Electronics?\n\n"
            "Try sending:\n"
            "• A DN number (like 6243699261)\n"
            "• A dealer name\n"
            "• 'Help' for commands",
            error=False
        )
    
    # ============================================================
    # EXISTING HANDLERS (KEEP YOUR ORIGINAL IMPLEMENTATIONS)
    # ============================================================
    
    async def _handle_dealer(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Original dealer handler - keep as is"""
        # ... [Your existing _handle_dealer implementation]
        pass
    
    async def _handle_dn(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Original DN handler - keep as is"""
        # ... [Your existing _handle_dn implementation]
        pass
    
    async def _handle_pending(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Original pending handler - keep as is"""
        # ... [Your existing _handle_pending implementation]
        pass
    
    async def _handle_groq(self, message: str, decision: RoutingDecision) -> Dict[str, Any]:
        """Original Groq handler - keep as is"""
        # ... [Your existing _handle_groq implementation]
        pass
    
    def _format_dealer_suggestions(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Original formatter - keep as is"""
        # ... [Your existing _format_dealer_suggestions implementation]
        pass
    
    def _format_pending_response(self, records: List, pending_type: str) -> str:
        """Original formatter - keep as is"""
        # ... [Your existing _format_pending_response implementation]
        pass
    
    def _format_response(self, original_message: str, data: Any, error: bool = False) -> Dict[str, Any]:
        """Original formatter - keep as is"""
        # ... [Your existing _format_response implementation]
        pass
    
    def get_system_health(self) -> Dict[str, Any]:
        """Get comprehensive system health"""
        service_status = ServiceLoader.get_service_status()
        return {
            "status": "healthy" if all(s['loaded'] for s in service_status.values()) else "degraded",
            "version": "8.5",
            "services": {
                "dealer": {
                    "available": service_status['dealer']['loaded'],
                    "circuit_breaker": service_status['dealer']['circuit_breaker']['state'],
                    "failure_count": service_status['dealer']['circuit_breaker']['failure_count']
                },
                "dn": {
                    "available": service_status['dn']['loaded'],
                    "circuit_breaker": service_status['dn']['circuit_breaker']['state'],
                    "failure_count": service_status['dn']['circuit_breaker']['failure_count']
                },
                "groq": {
                    "available": service_status['groq']['loaded'],
                    "circuit_breaker": service_status['groq']['circuit_breaker']['state'],
                    "failure_count": service_status['groq']['circuit_breaker']['failure_count']
                }
            },
            "dealer_count": len(DealerResolver.get_all_dealers()),
            "timestamp": datetime.now().isoformat()
        }

# ============================================================
# SINGLETON
# ============================================================

_whatsapp_provider_service = None
_provider_service_lock = threading.Lock()

def get_whatsapp_provider_service() -> WhatsAppProviderService:
    global _whatsapp_provider_service
    if _whatsapp_provider_service is None:
        with _provider_service_lock:
            if _whatsapp_provider_service is None:
                try:
                    _whatsapp_provider_service = WhatsAppProviderService()
                    logger.info("✅ WhatsAppProviderService initialized (v8.5)")
                except Exception as e:
                    logger.exception(f"❌ Initialization failed: {e}")
                    raise
    return _whatsapp_provider_service

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    'WhatsAppProviderService',
    'get_whatsapp_provider_service',
    'ServiceStatus',
    'RoutingDecision',
    'IntentDetectionEngine',
    'DealerResolver',
    'ServiceLoader',
    'CircuitBreaker'
]

logger.info("=" * 70)
logger.info("AI Provider Service v8.5 - ENTERPRISE FIXED")
logger.info("=" * 70)
logger.info("✅ Circuit Breaker Pattern")
logger.info("✅ Retry Logic with Exponential Backoff")
logger.info("✅ Service Loader with 5 fallback paths")
logger.info("✅ Dynamic Module Loading")
logger.info("✅ Fuzzy Dealer Matching (if Levenshtein installed)")
logger.info("✅ Comprehensive Health Checks")
logger.info("=" * 70)
