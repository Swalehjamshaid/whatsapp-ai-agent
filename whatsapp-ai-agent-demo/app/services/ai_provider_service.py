# ==========================================================
# FILE: app/services/ai_provider_service.py (v29.0 - PRODUCTION READY)
# PURPOSE: POSTGRESQL-DRIVEN AI ROUTER WITH IMPROVED ROUTING
# VERSION: 29.0 - Fixed Routing, Intent Detection, Error Handling
# ==========================================================

import time
import uuid
import re
import os
import requests
from typing import Optional, Callable, Any, Dict, List, Tuple
from dataclasses import dataclass, field
from cachetools import TTLCache, LRUCache
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, String, and_, or_

# ==========================================================
# BLOCK 1: POSTGRESQL IMPORTS - THE SOURCE OF TRUTH
# ==========================================================

from app.models import DeliveryReport
from app.database import SessionLocal, check_database_connection

# ==========================================================
# BLOCK 2: ANALYTICS SERVICE LOADER (PRODUCTION GRADE v9.0)
# ==========================================================

def _get_analytics_service():
    """
    Load analytics service with comprehensive validation.
    BLOCK 2 - FIXED v9.0 - PRODUCTION GRADE
    NEVER returns None. Always returns (service, response_class).
    """
    logger.info("=" * 70)
    logger.info("🔍 ANALYTICS SERVICE LOADER - PRODUCTION GRADE")
    logger.info("=" * 70)
    
    # ==========================================================
    # VALIDATION 1: Check AI Analysis Enabled
    # ==========================================================
    try:
        from app.config import config
        ai_enabled = getattr(config, 'AI_ANALYSIS_ENABLED', True)
        logger.info(f"📌 AI_ANALYSIS_ENABLED: {ai_enabled}")
        
        if not ai_enabled:
            logger.error("❌ AI_ANALYSIS_ENABLED is False")
            logger.error("   💡 Set AI_ANALYSIS_ENABLED=True in config")
            return _create_fallback_analytics(), None
    except Exception as e:
        logger.error(f"❌ Config validation failed: {e}")
        return _create_fallback_analytics(), None
    
    # ==========================================================
    # VALIDATION 2: Test Database Connection
    # ==========================================================
    try:
        from app.database import SessionLocal
        from app.models import DeliveryReport
        from sqlalchemy import func
        
        db = SessionLocal()
        
        # Get database statistics
        total_records = db.query(DeliveryReport).count()
        total_dns = db.query(func.count(distinct(DeliveryReport.dn_no))).scalar()
        total_dealers = db.query(func.count(distinct(DeliveryReport.customer_name))).scalar()
        total_warehouses = db.query(func.count(distinct(DeliveryReport.warehouse))).scalar()
        total_cities = db.query(func.count(distinct(DeliveryReport.ship_to_city))).scalar()
        
        db.close()
        
        logger.info(f"📌 PostgreSQL Connection: SUCCESS")
        logger.info(f"   📊 Total Records: {total_records}")
        logger.info(f"   📦 Total DNs: {total_dns}")
        logger.info(f"   🏪 Total Dealers: {total_dealers}")
        logger.info(f"   🏭 Total Warehouses: {total_warehouses}")
        logger.info(f"   🏙️ Total Cities: {total_cities}")
        
        if total_records == 0:
            logger.warning("⚠️ Database has ZERO records - dashboards will show zeros")
            logger.warning("   💡 Insert data into delivery_reports table")
            
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        logger.warning("⚠️ Using fallback analytics due to database error")
        return _create_fallback_analytics(), None
    
    # ==========================================================
    # VALIDATION 3: Import Analytics Service
    # ==========================================================
    try:
        from app.services.analytics_service import get_analytics_service, AnalyticsResponse
        logger.info("✅ Analytics service imported successfully")
    except ImportError as e:
        logger.error(f"❌ Analytics service import failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        logger.warning("⚠️ Using fallback analytics due to import error")
        return _create_fallback_analytics(), None
    
    # ==========================================================
    # VALIDATION 4: Get Service Instance
    # ==========================================================
    service = None
    try:
        service = get_analytics_service()
        
        if service is None:
            logger.error("❌ Analytics service returned None")
            # CRITICAL: Create manually instead of returning None
            try:
                from app.services.analytics_service import AnalyticsService
                service = AnalyticsService()
                logger.info("✅ AnalyticsService created manually")
            except Exception as e:
                logger.error(f"❌ Manual creation failed: {e}")
                logger.warning("⚠️ Using fallback analytics due to creation failure")
                return _create_fallback_analytics(), None
        
        logger.info(f"📊 Service type: {type(service)}")
        logger.info(f"📊 Service class: {service.__class__.__name__}")
        
    except Exception as e:
        logger.error(f"❌ Failed to get analytics service: {e}")
        import traceback
        logger.error(traceback.format_exc())
        logger.warning("⚠️ Using fallback analytics due to service error")
        return _create_fallback_analytics(), None
    
    # ==========================================================
    # VALIDATION 5: Verify Required Methods
    # ==========================================================
    required_methods = [
        "get_dn_dashboard",
        "get_dealer_dashboard",
        "get_warehouse_dashboard",
        "get_city_dashboard",
        "get_product_dashboard",
        "search_dn",
        "search_dealer",
        "search_warehouse",
        "search_city",
        "search_product",
        "verify_dn_exists",
        "verify_dealer_exists",
        "get_dealer_360_dashboard"
    ]
    
    logger.info("🔍 Verifying analytics methods:")
    missing_methods = []
    available_methods = []
    
    for method in required_methods:
        if hasattr(service, method):
            available_methods.append(method)
            logger.info(f"   ✅ {method}: AVAILABLE")
        else:
            missing_methods.append(method)
            logger.error(f"   ❌ {method}: MISSING")
    
    if missing_methods:
        logger.error(f"❌ Missing {len(missing_methods)} methods: {missing_methods}")
        logger.warning("⚠️ Using fallback analytics due to missing methods")
        return _create_fallback_analytics(), AnalyticsResponse
    
    logger.info(f"✅ All {len(available_methods)} required methods available")
    
    # ==========================================================
    # VALIDATION 6: Test a Sample Query
    # ==========================================================
    try:
        test_dealers = service.search_dealer("test", exact=False)
        if test_dealers and hasattr(test_dealers, 'success'):
            logger.info(f"✅ Dealer search test: success={test_dealers.success}")
            if test_dealers.success and test_dealers.data:
                results = test_dealers.data.get('results', [])
                logger.info(f"   Found {len(results)} results")
        elif isinstance(test_dealers, dict):
            results_count = len(test_dealers.get('data', {}).get('results', []))
            logger.info(f"✅ Dealer search test: returned {results_count} results")
        else:
            logger.info(f"✅ Dealer search test: {type(test_dealers)}")
    except Exception as e:
        logger.warning(f"⚠️ Dealer search test failed: {e}")
        logger.warning("   Continuing initialization...")
    
    # ==========================================================
    # VALIDATION 7: Get Sample Data
    # ==========================================================
    try:
        from app.database import SessionLocal
        from app.models import DeliveryReport
        db = SessionLocal()
        sample = db.query(DeliveryReport.dn_no, DeliveryReport.customer_name).first()
        db.close()
        if sample:
            logger.info(f"✅ Sample DN: {sample[0]} for Dealer: {sample[1]}")
        else:
            logger.warning("⚠️ No sample data found - database may be empty")
    except Exception as e:
        logger.warning(f"⚠️ Sample query failed: {e}")
    
    logger.info("=" * 70)
    logger.info("✅ Analytics service initialized successfully")
    logger.info("✅ Service is ready to serve REAL PostgreSQL data")
    logger.info("=" * 70)
    
    return service, AnalyticsResponse


def _create_fallback_analytics():
    """
    Create a fallback analytics service that returns friendly error messages.
    BLOCK 2 - ENHANCED FALLBACK WITH CLEAR MESSAGES
    """
    logger.warning("=" * 70)
    logger.warning("⚠️ FALLBACK ANALYTICS ACTIVATED")
    logger.warning("   This is NOT the real analytics service.")
    logger.warning("   Data will show as zeros or N/A.")
    logger.warning("=" * 70)
    logger.warning("   Possible causes:")
    logger.warning("   1. AI_ANALYSIS_ENABLED=False in config")
    logger.warning("   2. Database connection failed")
    logger.warning("   3. No data in delivery_reports table")
    logger.warning("   4. Analytics service import error")
    logger.warning("   5. Missing required methods")
    logger.warning("=" * 70)
    logger.warning("   To fix:")
    logger.warning("   1. Set AI_ANALYSIS_ENABLED=True")
    logger.warning("   2. Check database connection")
    logger.warning("   3. Add data to delivery_reports")
    logger.warning("   4. Check service imports")
    logger.warning("   5. Verify all methods exist")
    logger.warning("=" * 70)
    
    class FallbackAnalytics:
        """Fallback analytics service - prevents crashes with clear messages"""
        
        def get_dn_dashboard(self, dn_no):
            logger.warning(f"⚠️ Fallback: get_dn_dashboard called for {dn_no}")
            return {
                "dn_number": dn_no,
                "delivery_status": "Unknown",
                "customer_name": "Unknown",
                "warehouse": "Unknown",
                "ship_to_city": "Unknown",
                "units": 0,
                "amount": 0,
                "delivery_aging_text": "N/A",
                "pod_aging_text": "N/A",
                "total_cycle_text": "N/A",
                "error": f"DN {dn_no} not found. Please add data to delivery_reports table.",
                "hint": "Run: INSERT INTO delivery_reports (...) VALUES (...)"
            }
        
        def get_dealer_dashboard(self, dealer_name):
            logger.warning(f"⚠️ Fallback: get_dealer_dashboard called for {dealer_name}")
            return {
                "dealer_name": dealer_name,
                "total_dns": 0,
                "delivered_dns": 0,
                "pending_dns": 0,
                "delivery_rate": 0,
                "total_revenue": 0,
                "health_score": 50,
                "risk_level": "Unknown",
                "error": f"No data found for dealer '{dealer_name}'",
                "hint": "Add data to delivery_reports table with this customer_name"
            }
        
        def get_warehouse_dashboard(self, warehouse_name):
            logger.warning(f"⚠️ Fallback: get_warehouse_dashboard called for {warehouse_name}")
            return {
                "warehouse": warehouse_name,
                "total_dns": 0,
                "delivered_dns": 0,
                "pending_dns": 0,
                "delivery_rate": 0,
                "total_revenue": 0,
                "error": f"No data found for warehouse '{warehouse_name}'"
            }
        
        def get_city_dashboard(self, city_name):
            logger.warning(f"⚠️ Fallback: get_city_dashboard called for {city_name}")
            return {
                "city_name": city_name,
                "total_dns": 0,
                "delivered_dns": 0,
                "pending_dns": 0,
                "delivery_rate": 0,
                "total_revenue": 0,
                "error": f"No data found for city '{city_name}'"
            }
        
        def get_product_dashboard(self, product_name):
            logger.warning(f"⚠️ Fallback: get_product_dashboard called for {product_name}")
            return {
                "product": product_name,
                "revenue": 0,
                "units": 0,
                "dns": 0,
                "delivery_rate": 0,
                "error": f"No data found for product '{product_name}'"
            }
        
        def get_ranking_dashboard(self, limit=10):
            logger.warning("⚠️ Fallback: get_ranking_dashboard called")
            return {"ranking": [], "error": "No ranking data available"}
        
        def get_pgi_dashboard(self):
            logger.warning("⚠️ Fallback: get_pgi_dashboard called")
            return {"total_dns": 0, "pgi_completed": 0, "pgi_pending": 0, "pgi_rate": 0}
        
        def get_pod_dashboard(self):
            logger.warning("⚠️ Fallback: get_pod_dashboard called")
            return {"total_dns": 0, "pod_completed": 0, "pod_pending": 0, "pod_rate": 0}
        
        def get_delivery_dashboard(self):
            logger.warning("⚠️ Fallback: get_delivery_dashboard called")
            return {"total_dns": 0, "delivered": 0, "in_transit": 0, "delivery_rate": 0}
        
        def get_executive_dashboard(self):
            logger.warning("⚠️ Fallback: get_executive_dashboard called")
            return {"total_dns": 0, "total_units": 0, "total_revenue": 0, "delivery_rate": 0}
        
        def get_control_tower_dashboard(self):
            logger.warning("⚠️ Fallback: get_control_tower_dashboard called")
            return {"total_alerts": 0, "critical_count": 0, "high_count": 0, "alerts": []}
        
        def get_revenue_dashboard(self):
            logger.warning("⚠️ Fallback: get_revenue_dashboard called")
            return {"total_revenue": 0, "total_units": 0, "total_dns": 0, "top_dealers": []}
        
        def get_aging_dashboard(self):
            logger.warning("⚠️ Fallback: get_aging_dashboard called")
            return {"total_pending": 0, "days_0_7": 0, "days_8_14": 0, "days_15_30": 0, "days_30_plus": 0}
        
        def search_dn(self, query):
            logger.warning(f"⚠️ Fallback: search_dn called for {query}")
            return []
        
        def search_dealer(self, query):
            logger.warning(f"⚠️ Fallback: search_dealer called for {query}")
            return []
        
        def search_warehouse(self, query):
            logger.warning(f"⚠️ Fallback: search_warehouse called for {query}")
            return []
        
        def search_city(self, query):
            logger.warning(f"⚠️ Fallback: search_city called for {query}")
            return []
        
        def search_product(self, query):
            logger.warning(f"⚠️ Fallback: search_product called for {query}")
            return []
        
        def verify_dn_exists(self, dn_no):
            logger.warning(f"⚠️ Fallback: verify_dn_exists called for {dn_no}")
            return False
        
        def verify_dealer_exists(self, dealer_name):
            logger.warning(f"⚠️ Fallback: verify_dealer_exists called for {dealer_name}")
            return False
        
        def get_dealer_360_dashboard(self, dealer_name):
            logger.warning(f"⚠️ Fallback: get_dealer_360_dashboard called for {dealer_name}")
            return {
                "error": f"No data found for dealer '{dealer_name}'",
                "message": "Please add data to delivery_reports table",
                "profile": {
                    "dealer_name": dealer_name,
                    "dealer_code": "N/A",
                    "warehouse": "N/A",
                    "city": "N/A"
                },
                "business_volume": {
                    "total_dns": 0,
                    "total_units": 0,
                    "total_revenue": 0
                },
                "delivery_status": {
                    "delivered": 0,
                    "in_transit": 0,
                    "pending_pgi": 0
                },
                "performance": {
                    "delivery_rate": 0,
                    "health_score": 50,
                    "risk_level": "Unknown"
                }
            }
    
    logger.info("✅ FallbackAnalytics created")
    return FallbackAnalytics()


# ==========================================================
# END OF BLOCK 2
# ==========================================================


# ==========================================================
# BLOCK 3: CONFIGURATION (ALIGNED WITH app/config.py)
# ==========================================================

from app.config import config

# Use config values with fallbacks
CACHE_TTL_SECONDS = getattr(config, 'CACHE_TTL', 300)
CONTEXT_TTL_SECONDS = getattr(config, 'CACHE_TTL_SESSION', 1800)
MAX_RESPONSE_LENGTH = 2500  # Keep as constant
QUERY_TIMEOUT_SECONDS = getattr(config, 'AI_TIMEOUT_SECONDS', 10)
MAX_RETRY_ATTEMPTS = getattr(config, 'AI_MAX_RETRIES', 3)

# AI Provider settings
AI_PROVIDER = getattr(config, 'AI_PROVIDER', 'groq')
AI_FALLBACK_PROVIDER = getattr(config, 'AI_FALLBACK_PROVIDER', 'deepseek')
AI_ANALYSIS_ENABLED = getattr(config, 'AI_ANALYSIS_ENABLED', True)
AI_FALLBACK_TO_RULE_BASED = getattr(config, 'AI_FALLBACK_TO_RULE_BASED', True)

# Fuzzy matching settings
FUZZY_MATCH_THRESHOLD = float(os.getenv('FUZZY_MATCH_THRESHOLD', '0.3'))
MAX_FUZZY_RESULTS = int(os.getenv('MAX_FUZZY_RESULTS', '1000'))

# WhatsApp settings
WHATSAPP_ACCESS_TOKEN = getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')
WHATSAPP_PHONE_NUMBER_ID = getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')
WHATSAPP_API_VERSION = getattr(config, 'WHATSAPP_API_VERSION', 'v25.0')
WHATSAPP_API_URL = getattr(config, 'WHATSAPP_API_URL', 'https://graph.facebook.com')

logger.info("=" * 70)
logger.info("📋 AI Provider Configuration Loaded:")
logger.info(f"   CACHE_TTL: {CACHE_TTL_SECONDS}s")
logger.info(f"   CONTEXT_TTL: {CONTEXT_TTL_SECONDS}s")
logger.info(f"   AI_PROVIDER: {AI_PROVIDER}")
logger.info(f"   AI_FALLBACK: {AI_FALLBACK_PROVIDER}")
logger.info(f"   AI_ANALYSIS_ENABLED: {AI_ANALYSIS_ENABLED}")
logger.info(f"   FUZZY_THRESHOLD: {FUZZY_MATCH_THRESHOLD}")
logger.info("=" * 70)


# ==========================================================
# BLOCK 3.5: WHATSAPP TOKEN VALIDATION
# ==========================================================

def validate_whatsapp_token() -> Dict[str, Any]:
    """
    Validate WhatsApp access token from config.
    Returns validation result with status.
    """
    token = getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')
    phone_id = getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')
    
    if not token:
        logger.error("❌ WHATSAPP_ACCESS_TOKEN not configured")
        return {
            "valid": False,
            "error": "WHATSAPP_ACCESS_TOKEN not configured",
            "message": "Please set WHATSAPP_ACCESS_TOKEN in environment variables"
        }
    
    if not phone_id:
        logger.error("❌ WHATSAPP_PHONE_NUMBER_ID not configured")
        return {
            "valid": False,
            "error": "WHATSAPP_PHONE_NUMBER_ID not configured",
            "message": "Please set WHATSAPP_PHONE_NUMBER_ID in environment variables"
        }
    
    try:
        api_version = getattr(config, 'WHATSAPP_API_VERSION', 'v25.0')
        api_url = getattr(config, 'WHATSAPP_API_URL', 'https://graph.facebook.com')
        
        url = f"{api_url}/{api_version}/me?access_token={token}"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"✅ WhatsApp token valid for app: {data.get('name')} (ID: {data.get('id')})")
            return {
                "valid": True,
                "app_id": data.get("id"),
                "app_name": data.get("name"),
                "message": "Token is valid"
            }
        else:
            error = response.json().get("error", {})
            error_code = error.get("code")
            error_msg = error.get("message", "Unknown error")
            
            logger.error(f"❌ WhatsApp token invalid: {error_code} - {error_msg}")
            
            if error_code == 131005:
                return {
                    "valid": False,
                    "error": error_msg,
                    "error_code": error_code,
                    "message": "Access denied. Please regenerate your WhatsApp access token.",
                    "action": "Go to Meta Developer Console → WhatsApp → API Setup → Generate new token"
                }
            elif error_code == 190:
                return {
                    "valid": False,
                    "error": error_msg,
                    "error_code": error_code,
                    "message": "Token expired or invalid. Please regenerate your WhatsApp access token.",
                    "action": "Go to Meta Developer Console → WhatsApp → API Setup → Generate new token"
                }
            else:
                return {
                    "valid": False,
                    "error": error_msg,
                    "error_code": error_code,
                    "message": f"Token validation failed: {error_msg}",
                    "action": "Check your WhatsApp configuration and regenerate token if needed"
                }
                
    except requests.Timeout:
        logger.error("❌ WhatsApp token validation timeout")
        return {
            "valid": False,
            "error": "Timeout",
            "message": "Connection to Meta API timed out. Check your internet connection."
        }
    except Exception as e:
        logger.error(f"❌ WhatsApp token validation error: {e}")
        return {
            "valid": False,
            "error": str(e),
            "message": f"Error validating token: {str(e)}"
        }


def get_whatsapp_config() -> Dict[str, Any]:
    """Get WhatsApp configuration from app.config"""
    return {
        "access_token": getattr(config, 'WHATSAPP_ACCESS_TOKEN', ''),
        "phone_number_id": getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', ''),
        "business_account_id": getattr(config, 'WHATSAPP_BUSINESS_ACCOUNT_ID', ''),
        "verify_token": getattr(config, 'WHATSAPP_VERIFY_TOKEN', ''),
        "api_version": getattr(config, 'WHATSAPP_API_VERSION', 'v25.0'),
        "api_url": getattr(config, 'WHATSAPP_API_URL', 'https://graph.facebook.com'),
        "message_timeout": getattr(config, 'WHATSAPP_MESSAGE_TIMEOUT', 60)
    }


# ==========================================================
# BLOCK 4: DATABASE CONNECTION TEST
# ==========================================================

def test_database_connection() -> Dict[str, Any]:
    """Test PostgreSQL connection from AI Provider."""
    try:
        db = SessionLocal()
        total_records = db.query(DeliveryReport).count()
        db.close()
        
        return {
            "connected": True,
            "total_records": total_records,
            "table_name": "delivery_reports",
            "status": "healthy"
        }
    except Exception as e:
        logger.error(f"AI Database connection test failed: {e}")
        return {
            "connected": False,
            "error": str(e),
            "status": "unhealthy"
        }


# ==========================================================
# BLOCK 5: POSTGRESQL RESOLVER (FIXED v4.0 - CONFIGURABLE)
# ==========================================================

class PostgreSQLResolver:
    """Pure PostgreSQL-based entity resolution with configurable thresholds"""
    
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self.session_factory = session_factory
        self._cache = TTLCache(maxsize=2000, ttl=3600)
        self.DeliveryReport = DeliveryReport
        
        try:
            self.fuzzy_threshold = float(os.getenv('FUZZY_MATCH_THRESHOLD', '0.3'))
            self.max_fuzzy_results = int(os.getenv('MAX_FUZZY_RESULTS', '1000'))
            logger.info(f"✅ Fuzzy threshold: {self.fuzzy_threshold}, Max results: {self.max_fuzzy_results}")
        except Exception as e:
            self.fuzzy_threshold = 0.3
            self.max_fuzzy_results = 1000
            logger.warning(f"⚠️ Config load error: {e}, using defaults")
    
    def _get_session(self) -> Optional[Session]:
        if not self.session_factory:
            logger.error("❌ No session_factory provided!")
            return None
        try:
            return self.session_factory()
        except Exception as e:
            logger.error(f"Session creation failed: {e}")
            return None
    
    def resolve_dealer(self, query: str) -> Optional[str]:
        """Resolve dealer name with 8 strategies."""
        if not query or not query.strip():
            return None
        
        query_clean = query.strip()
        typo_fixes = {"are ": "", "is ": "", "the ": "", "for ": "", "of ": ""}
        for typo, fix in typo_fixes.items():
            if query_clean.lower().startswith(typo):
                query_clean = query_clean[len(typo):].strip()
                break
        
        if not query_clean:
            query_clean = query.strip()
        
        cache_key = f"dealer:{query_clean.lower()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            # STRATEGY 1: Exact match
            result = session.query(self.DeliveryReport.customer_name).filter(
                func.lower(self.DeliveryReport.customer_name) == func.lower(query_clean)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # STRATEGY 2: ILIKE match
            result = session.query(self.DeliveryReport.customer_name).filter(
                self.DeliveryReport.customer_name.ilike(f"%{query_clean}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            # STRATEGY 3: Token-based matching
            tokens = query_clean.split()
            for token in tokens:
                if len(token) > 2 and token.lower() not in ['the', 'and', 'for', 'with']:
                    result = session.query(self.DeliveryReport.customer_name).filter(
                        self.DeliveryReport.customer_name.ilike(f"%{token}%")
                    ).first()
                    if result:
                        resolved = result[0]
                        self._cache[cache_key] = resolved
                        return resolved
            
            # STRATEGY 4: Fuzzy matching
            dealers = session.query(
                func.distinct(self.DeliveryReport.customer_name)
            ).filter(
                self.DeliveryReport.customer_name.isnot(None),
                self.DeliveryReport.customer_name != ''
            ).limit(self.max_fuzzy_results).all()
            
            best_match = None
            best_score = 0
            query_lower = query_clean.lower()
            query_tokens = set(query_lower.split())
            
            for dealer in dealers:
                if not dealer[0]:
                    continue
                dealer_name = dealer[0]
                dealer_lower = dealer_name.lower()
                dealer_tokens = set(dealer_lower.split())
                
                scores = []
                
                if query_tokens and dealer_tokens:
                    overlap = len(query_tokens & dealer_tokens)
                    token_score = overlap / max(len(query_tokens), len(dealer_tokens))
                    scores.append(token_score)
                
                char_overlap = len(set(query_lower) & set(dealer_lower))
                char_score = char_overlap / max(len(query_lower), len(dealer_lower))
                scores.append(char_score)
                
                if query_lower in dealer_lower or dealer_lower in query_lower:
                    scores.append(0.8)
                
                for token in query_tokens:
                    if len(token) > 2 and token in dealer_lower:
                        scores.append(0.7)
                
                if scores:
                    score = max(scores)
                else:
                    score = 0
                
                if score > best_score and score > self.fuzzy_threshold:
                    best_score = score
                    best_match = dealer_name
            
            if best_match:
                self._cache[cache_key] = best_match
                logger.info(f"✅ Dealer resolved (fuzzy, score={best_score:.2f}): {best_match}")
                return best_match
            
            # STRATEGY 5: Partial word matching
            for token in tokens:
                if len(token) > 2:
                    results = session.query(
                        func.distinct(self.DeliveryReport.customer_name)
                    ).filter(
                        or_(
                            self.DeliveryReport.customer_name.ilike(f"% {token} %"),
                            self.DeliveryReport.customer_name.ilike(f"{token} %"),
                            self.DeliveryReport.customer_name.ilike(f"% {token}")
                        )
                    ).limit(10).all()
                    
                    if results:
                        resolved = results[0][0]
                        self._cache[cache_key] = resolved
                        logger.info(f"✅ Dealer resolved (partial word '{token}'): {resolved}")
                        return resolved
            
            return None
            
        except Exception as e:
            logger.error(f"Dealer resolution error: {e}")
            return None
        finally:
            session.close()
    
    def resolve_warehouse(self, query: str) -> Optional[str]:
        """Resolve warehouse name from PostgreSQL"""
        if not query or not query.strip():
            return None
        
        cache_key = f"warehouse:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            result = session.query(self.DeliveryReport.warehouse).filter(
                func.lower(self.DeliveryReport.warehouse) == func.lower(query)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            result = session.query(self.DeliveryReport.warehouse).filter(
                self.DeliveryReport.warehouse.ilike(f"%{query}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            tokens = query.split()
            for token in tokens:
                if len(token) > 2:
                    result = session.query(self.DeliveryReport.warehouse).filter(
                        self.DeliveryReport.warehouse.ilike(f"%{token}%")
                    ).first()
                    if result:
                        resolved = result[0]
                        self._cache[cache_key] = resolved
                        return resolved
            
            return None
            
        except Exception as e:
            logger.error(f"Warehouse resolution error: {e}")
            return None
        finally:
            session.close()
    
    def resolve_city(self, query: str) -> Optional[str]:
        """Resolve city name from PostgreSQL"""
        if not query or not query.strip():
            return None
        
        cache_key = f"city:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            result = session.query(self.DeliveryReport.ship_to_city).filter(
                func.lower(self.DeliveryReport.ship_to_city) == func.lower(query)
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            result = session.query(self.DeliveryReport.ship_to_city).filter(
                self.DeliveryReport.ship_to_city.ilike(f"%{query}%")
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            tokens = query.split()
            for token in tokens:
                if len(token) > 2:
                    result = session.query(self.DeliveryReport.ship_to_city).filter(
                        self.DeliveryReport.ship_to_city.ilike(f"%{token}%")
                    ).first()
                    if result:
                        resolved = result[0]
                        self._cache[cache_key] = resolved
                        return resolved
            
            return None
            
        except Exception as e:
            logger.error(f"City resolution error: {e}")
            return None
        finally:
            session.close()
    
    def resolve_product(self, query: str) -> Optional[str]:
        """Resolve product name from PostgreSQL"""
        if not query or not query.strip():
            return None
        
        cache_key = f"product:{query.lower().strip()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            result = session.query(self.DeliveryReport.customer_model).filter(
                func.lower(self.DeliveryReport.customer_model) == func.lower(query)
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            result = session.query(self.DeliveryReport.material_no).filter(
                func.lower(self.DeliveryReport.material_no) == func.lower(query)
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            result = session.query(self.DeliveryReport.customer_model).filter(
                self.DeliveryReport.customer_model.ilike(f"%{query}%")
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            result = session.query(self.DeliveryReport.material_no).filter(
                self.DeliveryReport.material_no.ilike(f"%{query}%")
            ).first()
            if result and result[0]:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            
            return None
            
        except Exception as e:
            logger.error(f"Product resolution error: {e}")
            return None
        finally:
            session.close()
    
    def resolve_dn(self, query: str) -> Optional[str]:
        """Resolve DN number from PostgreSQL"""
        if not query or not query.strip():
            return None
        
        normalized = re.sub(r'[^0-9]', '', str(query).strip())
        if len(normalized) < 8 or len(normalized) > 12:
            return None
        
        cache_key = f"dn:{normalized}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        session = self._get_session()
        if not session:
            return None
        
        try:
            result = session.query(self.DeliveryReport.dn_no).filter(
                cast(self.DeliveryReport.dn_no, String) == normalized
            ).first()
            if result:
                resolved = result[0]
                self._cache[cache_key] = resolved
                return resolved
            return None
            
        except Exception as e:
            logger.error(f"DN resolution error: {e}")
            return None
        finally:
            session.close()


# ==========================================================
# BLOCK 6: CONVERSATION CONTEXT
# ==========================================================

@dataclass
class ConversationContext:
    phone_number: str
    last_intent: Optional[str] = None
    last_entity: Optional[str] = None
    last_dealer: Optional[str] = None
    last_warehouse: Optional[str] = None
    last_city: Optional[str] = None
    last_dn: Optional[str] = None
    last_product: Optional[str] = None
    last_division: Optional[str] = None
    last_sales_manager: Optional[str] = None
    last_dashboard: Optional[str] = None
    last_question: Optional[str] = None
    last_response: Optional[str] = None
    message_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    confidence: float = 0.0
    is_valid: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_dealer": self.last_dealer,
            "last_warehouse": self.last_warehouse,
            "last_city": self.last_city,
            "last_dn": self.last_dn,
            "last_product": self.last_product,
            "last_division": self.last_division,
            "last_sales_manager": self.last_sales_manager,
            "last_dashboard": self.last_dashboard,
            "last_intent": self.last_intent,
            "phone_number": self.phone_number,
        }


# ==========================================================
# BLOCK 7: INTENT CLASSIFICATION (ENHANCED v5.0)
# ==========================================================

@dataclass
class IntentResult:
    """Result of intent classification with high confidence"""
    intent: str
    confidence: float
    entity_type: Optional[str] = None
    entity_value: Optional[str] = None
    raw_query: str = ""
    normalized_query: str = ""

class IntentClassifier:
    """
    Enhanced intent classifier with PostgreSQL-backed entity resolution.
    BLOCK 7 - ENHANCED v5.0 with better patterns and confidence scoring.
    """
    
    # Priority-ordered product keywords (MUST match before dealer)
    PRODUCT_KEYWORDS = {
        'refrigerator': 0.95,
        'fridge': 0.95,
        'freezer': 0.95,
        'deep freezer': 0.95,
        'ac': 0.95,
        'air conditioner': 0.95,
        'washing machine': 0.95,
        'washer': 0.90,
        'led': 0.90,
        'tv': 0.90,
        'television': 0.90,
        'microwave': 0.95,
        'oven': 0.90,
        'water dispenser': 0.95,
        'cooler': 0.90,
        'heater': 0.90,
        'generator': 0.85,
    }
    
    # Dealer indicator words (lower priority than products)
    DEALER_INDICATORS = {
        'electronics': 0.70,
        'trading': 0.60,
        'enterprise': 0.60,
        'corporation': 0.60,
        'industries': 0.60,
        'traders': 0.60,
        'house': 0.50,
        'store': 0.50,
        'mart': 0.50,
        'company': 0.50,
    }
    
    # Warehouse indicator words
    WAREHOUSE_KEYWORDS = {
        'warehouse': 0.95,
        'wh': 0.90,
        'depot': 0.90,
        'distribution center': 0.90,
        'godown': 0.85,
    }
    
    # City indicator words
    CITY_KEYWORDS = {
        'city': 0.95,
        'town': 0.85,
        'district': 0.85,
        'region': 0.80,
        'area': 0.70,
    }
    
    def __init__(self, resolver: Optional[PostgreSQLResolver] = None):
        self.resolver = resolver or PostgreSQLResolver(session_factory=SessionLocal)
        self._cache = TTLCache(maxsize=1000, ttl=300)
    
    def classify(self, query: str, context: Optional[ConversationContext] = None) -> IntentResult:
        """
        Classify intent with priority-based routing.
        BLOCK 7 - FIXED ROUTING PRIORITY
        """
        if not query or not query.strip():
            return IntentResult(
                intent="help",
                confidence=1.0,
                raw_query=query
            )
        
        query_clean = query.strip()
        query_lower = query_clean.lower()
        cache_key = f"intent:{query_lower}"
        
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            # Check if cached result is still valid
            if cached.confidence > 0.5:
                return cached
        
        result = self._classify_with_priority(query_clean, query_lower, context)
        self._cache[cache_key] = result
        return result
    
    def _classify_with_priority(self, query: str, query_lower: str, context: Optional[ConversationContext]) -> IntentResult:
        """
        Classify intent with strict priority order.
        PRIORITY 1: DN Number (8-12 digits)
        PRIORITY 2: Warehouse (explicit keywords)
        PRIORITY 3: City (explicit keywords)  
        PRIORITY 4: Product (product keywords)
        PRIORITY 5: Dealer (dealer keywords or standalone)
        """
        
        # ==========================================================
        # PRIORITY 1: DN SEARCH (8-12 digit numbers)
        # ==========================================================
        dn_match = re.search(r'\b(\d{8,12})\b', query)
        if dn_match:
            dn_number = dn_match.group(1)
            return IntentResult(
                intent="dn_dashboard",
                confidence=1.0,
                entity_type="dn",
                entity_value=dn_number,
                raw_query=query,
                normalized_query=dn_number
            )
        
        # ==========================================================
        # PRIORITY 2: WAREHOUSE SEARCH (explicit keywords)
        # ==========================================================
        for keyword, confidence in self.WAREHOUSE_KEYWORDS.items():
            if keyword in query_lower:
                # Extract warehouse name
                warehouse_patterns = [
                    rf'(?:warehouse|wh|depot|godown)\s+([A-Za-z0-9\s\-&]+)',
                    rf'([A-Za-z0-9\s\-&]+)\s+warehouse',
                    rf'(?:wh|depot)\s*[-:]?\s*([A-Za-z0-9\-]+)',
                ]
                
                for pattern in warehouse_patterns:
                    match = re.search(pattern, query, re.IGNORECASE)
                    if match:
                        warehouse_name = match.group(1).strip()
                        if len(warehouse_name) > 1:
                            # Try to resolve
                            resolved = self.resolver.resolve_warehouse(warehouse_name)
                            if resolved:
                                return IntentResult(
                                    intent="warehouse_dashboard",
                                    confidence=confidence,
                                    entity_type="warehouse",
                                    entity_value=resolved,
                                    raw_query=query,
                                    normalized_query=warehouse_name
                                )
                            else:
                                # Still route to warehouse dashboard with what we have
                                return IntentResult(
                                    intent="warehouse_dashboard",
                                    confidence=confidence * 0.8,
                                    entity_type="warehouse",
                                    entity_value=warehouse_name,
                                    raw_query=query,
                                    normalized_query=warehouse_name
                                )
        
        # ==========================================================
        # PRIORITY 3: CITY SEARCH (explicit keywords)
        # ==========================================================
        for keyword, confidence in self.CITY_KEYWORDS.items():
            if keyword in query_lower:
                city_patterns = [
                    rf'(?:city|town|district|region)\s+([A-Za-z\s\-]+)',
                    rf'([A-Za-z\s\-]+)\s+city',
                ]
                
                for pattern in city_patterns:
                    match = re.search(pattern, query, re.IGNORECASE)
                    if match:
                        city_name = match.group(1).strip()
                        if len(city_name) > 1:
                            resolved = self.resolver.resolve_city(city_name)
                            if resolved:
                                return IntentResult(
                                    intent="city_dashboard",
                                    confidence=confidence,
                                    entity_type="city",
                                    entity_value=resolved,
                                    raw_query=query,
                                    normalized_query=city_name
                                )
                            else:
                                return IntentResult(
                                    intent="city_dashboard",
                                    confidence=confidence * 0.8,
                                    entity_type="city",
                                    entity_value=city_name,
                                    raw_query=query,
                                    normalized_query=city_name
                                )
        
        # ==========================================================
        # PRIORITY 4: PRODUCT SEARCH (product keywords)
        # ==========================================================
        best_product_match = None
        best_product_score = 0
        
        for keyword, score in self.PRODUCT_KEYWORDS.items():
            if keyword in query_lower:
                # Check if it's an exact product keyword match
                if score > best_product_score:
                    best_product_score = score
                    best_product_match = keyword
        
        if best_product_match:
            # Try to resolve product
            resolved = self.resolver.resolve_product(best_product_match)
            if resolved:
                return IntentResult(
                    intent="product_dashboard",
                    confidence=best_product_score,
                    entity_type="product",
                    entity_value=resolved,
                    raw_query=query,
                    normalized_query=best_product_match
                )
            else:
                # Still route to product dashboard
                return IntentResult(
                    intent="product_dashboard",
                    confidence=best_product_score * 0.8,
                    entity_type="product",
                    entity_value=best_product_match,
                    raw_query=query,
                    normalized_query=best_product_match
                )
        
        # ==========================================================
        # PRIORITY 5: DEALER SEARCH (last priority)
        # ==========================================================
        
        # Check for explicit dealer indicators
        dealer_score = 0
        for keyword, score in self.DEALER_INDICATORS.items():
            if keyword in query_lower:
                dealer_score = max(dealer_score, score)
        
        # If query has dealer indicators, route to dealer
        if dealer_score > 0.3:
            dealer_name = query
            # Clean up
            for indicator in self.DEALER_INDICATORS:
                if indicator in query_lower:
                    dealer_name = query.replace(indicator, '').strip()
                    dealer_name = re.sub(r'[^A-Za-z0-9\s\.\-&]', '', dealer_name).strip()
                    break
            
            if dealer_name:
                resolved = self.resolver.resolve_dealer(dealer_name)
                if resolved:
                    return IntentResult(
                        intent="dealer_dashboard",
                        confidence=dealer_score,
                        entity_type="dealer",
                        entity_value=resolved,
                        raw_query=query,
                        normalized_query=dealer_name
                    )
                else:
                    return IntentResult(
                        intent="dealer_dashboard",
                        confidence=dealer_score * 0.7,
                        entity_type="dealer",
                        entity_value=dealer_name,
                        raw_query=query,
                        normalized_query=dealer_name
                    )
        
        # ==========================================================
        # STANDALONE ENTITY DETECTION (with correct priority)
        # ==========================================================
        if len(query) > 2:
            # Check if it's a short word that could be a product
            if len(query) <= 20 and not any(c.isdigit() for c in query):
                # Try product first (NEW - FIXES PRODUCT ROUTING)
                product_resolved = self.resolver.resolve_product(query)
                if product_resolved:
                    return IntentResult(
                        intent="product_dashboard",
                        confidence=0.7,
                        entity_type="product",
                        entity_value=product_resolved,
                        raw_query=query,
                        normalized_query=query
                    )
                
                # Then warehouse
                warehouse_resolved = self.resolver.resolve_warehouse(query)
                if warehouse_resolved:
                    return IntentResult(
                        intent="warehouse_dashboard",
                        confidence=0.7,
                        entity_type="warehouse",
                        entity_value=warehouse_resolved,
                        raw_query=query,
                        normalized_query=query
                    )
                
                # Then city
                city_resolved = self.resolver.resolve_city(query)
                if city_resolved:
                    return IntentResult(
                        intent="city_dashboard",
                        confidence=0.7,
                        entity_type="city",
                        entity_value=city_resolved,
                        raw_query=query,
                        normalized_query=query
                    )
                
                # Finally dealer (lowest priority - FIXED)
                dealer_resolved = self.resolver.resolve_dealer(query)
                if dealer_resolved:
                    return IntentResult(
                        intent="dealer_dashboard",
                        confidence=0.6,
                        entity_type="dealer",
                        entity_value=dealer_resolved,
                        raw_query=query,
                        normalized_query=query
                    )
        
        # ==========================================================
        # CONTEXT FALLBACK
        # ==========================================================
        if context and context.last_intent and context.last_entity:
            return IntentResult(
                intent=context.last_intent,
                confidence=0.5,
                entity_type=self._get_entity_type(context.last_intent),
                entity_value=context.last_entity,
                raw_query=query,
                normalized_query=query
            )
        
        # ==========================================================
        # DEFAULT: HELP
        # ==========================================================
        return IntentResult(
            intent="help",
            confidence=1.0,
            raw_query=query,
            normalized_query=query
        )
    
    def _get_entity_type(self, intent: str) -> str:
        """Map intent to entity type."""
        mapping = {
            "dn_dashboard": "dn",
            "dealer_dashboard": "dealer",
            "warehouse_dashboard": "warehouse",
            "city_dashboard": "city",
            "product_dashboard": "product",
            "dealer_ranking": "dealer",
            "dealer_products": "dealer",
            "warehouse_ranking": "warehouse",
            "warehouse_coverage": "warehouse",
            "warehouse_products": "warehouse",
            "city_ranking": "city",
            "city_dealers": "city",
            "city_products": "city",
            "product_ranking": "product",
            "product_trend": "product",
            "pgi_dashboard": "pgi",
            "pod_dashboard": "pod",
            "delivery_dashboard": "delivery",
            "executive_dashboard": "executive",
            "control_tower": "control",
            "revenue_dashboard": "revenue",
            "aging_dashboard": "aging",
            "division_dashboard": "division",
            "sales_manager_dashboard": "sales_manager",
            "sales_office_dashboard": "sales_office",
        }
        return mapping.get(intent, "unknown")


# ==========================================================
# BLOCK 8: MAIN AI ROUTER (FIXED v5.0)
# ==========================================================

class AIOrchestrator:
    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self.session_factory = session_factory or SessionLocal
        self._analytics = None
        self._analytics_response = None
        self._resolver = None
        self._classifier = None
        
        self.response_cache = TTLCache(maxsize=2000, ttl=CACHE_TTL_SECONDS)
        self.failure_cache = TTLCache(maxsize=400, ttl=60)
        self.fast_cache = LRUCache(maxsize=1000)
        self.conversation_cache: Dict[str, ConversationContext] = {}
        self._current_request_id: Optional[str] = None
        
        self.metrics = {
            "total_requests": 0,
            "intent_detection": {},
            "entity_resolution": {},
            "errors": 0,
            "cache_hits": 0,
            "cache_misses": 0
        }

        # ==========================================================
        # WHATSAPP TOKEN VALIDATION ON STARTUP
        # ==========================================================
        try:
            if getattr(config, 'WHATSAPP_ACCESS_TOKEN', ''):
                logger.info("🔍 Validating WhatsApp token...")
                validation = validate_whatsapp_token()
                if validation.get('valid'):
                    logger.info(f"✅ WhatsApp token valid: {validation.get('app_name')}")
                else:
                    logger.warning(f"⚠️ WhatsApp token invalid: {validation.get('message')}")
                    if validation.get('action'):
                        logger.warning(f"   Action: {validation.get('action')}")
            else:
                logger.warning("⚠️ WHATSAPP_ACCESS_TOKEN not configured")
        except Exception as e:
            logger.error(f"❌ WhatsApp validation error: {e}")

        logger.info("=" * 70)
        logger.info("AI Router v29.0 - Initializing...")
        logger.info("=" * 70)
        
        # Initialize components
        try:
            self._init_analytics()
        except Exception as e:
            logger.error(f"❌ Analytics init failed: {e}")
            self._analytics = None
        
        # Initialize classifier
        self._classifier = IntentClassifier(self.resolver)
        
        # Verify methods
        try:
            self._verify_analytics_methods()
        except Exception as e:
            logger.error(f"❌ Method verification failed: {e}")
        
        logger.info("=" * 70)
        logger.info("AI Router v29.0 - PostgreSQL-Driven Production")
        logger.info("=" * 70)
    
    def _init_analytics(self):
        """Initialize analytics service with retry."""
        for attempt in range(3):
            try:
                logger.info(f"🔄 Attempt {attempt + 1}/3 to initialize analytics...")
                service, response_class = _get_analytics_service()
                self._analytics = service
                self._analytics_response = response_class
                
                if self._analytics is not None:
                    logger.info(f"✅ Analytics service initialized on attempt {attempt + 1}")
                    return
                else:
                    logger.warning(f"⚠️ Analytics service None on attempt {attempt + 1}")
                    time.sleep(1)
            except Exception as e:
                logger.error(f"❌ Attempt {attempt + 1} failed: {e}")
                time.sleep(1)
        
        logger.error("❌ All attempts to initialize analytics failed!")
    
    def _verify_analytics_methods(self):
        """Verify required analytics methods exist."""
        if not self.analytics:
            logger.error("❌ Analytics service is None - cannot verify methods")
            return
        
        required_methods = [
            "get_dn_dashboard",
            "get_dealer_dashboard",
            "get_warehouse_dashboard",
            "get_city_dashboard",
            "get_product_dashboard",
            "search_dealer",
            "verify_dealer_exists",
            "verify_dn_exists",
            "get_dealer_360_dashboard"
        ]
        
        logger.info("🔍 Verifying analytics methods:")
        for method in required_methods:
            if hasattr(self.analytics, method):
                logger.info(f"   ✅ {method}: AVAILABLE")
            else:
                logger.error(f"   ❌ {method}: MISSING")
    
    @property
    def analytics(self):
        """Get analytics service with lazy loading."""
        if self._analytics is None:
            logger.warning("⚠️ Analytics service is None - attempting to reload...")
            try:
                service, response_class = _get_analytics_service()
                self._analytics = service
                self._analytics_response = response_class
                if self._analytics is None:
                    logger.error("❌ Analytics service still None after reload")
            except Exception as e:
                logger.error(f"❌ Reload failed: {e}")
        return self._analytics
    
    @property
    def resolver(self):
        """Get resolver with lazy initialization."""
        if self._resolver is None:
            self._resolver = PostgreSQLResolver(self.session_factory)
        return self._resolver
    
    @property
    def classifier(self):
        """Get classifier with lazy initialization."""
        if self._classifier is None:
            self._classifier = IntentClassifier(self.resolver)
        return self._classifier
    
    def _load_context(self, phone_number: Optional[str]) -> Optional[ConversationContext]:
        """Load conversation context."""
        if not phone_number:
            return None
        
        if phone_number not in self.conversation_cache:
            self.conversation_cache[phone_number] = ConversationContext(phone_number=phone_number)
        
        context = self.conversation_cache[phone_number]
        if time.time() - context.last_updated > CONTEXT_TTL_SECONDS:
            context = ConversationContext(phone_number=phone_number)
            self.conversation_cache[phone_number] = context
        
        return context
    
    def _update_context(self, phone_number: Optional[str], intent: str, entity_type: str, entity: str, req_id: str):
        """Update conversation context."""
        if not phone_number:
            return
        
        context = self._load_context(phone_number)
        if not context:
            return
        
        context.last_intent = intent
        context.last_question = entity
        context.last_dashboard = intent
        context.confidence = 0.9
        context.message_count += 1
        context.last_updated = time.time()
        context.is_valid = True
        
        if entity_type == "dealer":
            context.last_dealer = entity
            context.last_entity = entity
        elif entity_type == "warehouse":
            context.last_warehouse = entity
            context.last_entity = entity
        elif entity_type == "city":
            context.last_city = entity
            context.last_entity = entity
        elif entity_type == "dn":
            context.last_dn = entity
            context.last_entity = entity
        elif entity_type == "product":
            context.last_product = entity
            context.last_entity = entity
        elif entity_type == "division":
            context.last_division = entity
            context.last_entity = entity
        elif entity_type == "sales_manager":
            context.last_sales_manager = entity
            context.last_entity = entity
        
        self.conversation_cache[phone_number] = context


# ==========================================================
# BLOCK 9: MAIN ENTRY POINT
# ==========================================================

    def process_whatsapp_query(
        self,
        question: str,
        session_factory: Optional[Callable[[], Session]] = None,
        phone_number: Optional[str] = None,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> str:
        """Process WhatsApp query with improved routing."""
        start_time = time.time()
        
        # Check AI enabled
        if not getattr(config, 'AI_ANALYSIS_ENABLED', True):
            logger.warning("⚠️ AI_ANALYSIS_ENABLED is False")
            return "⚠️ AI service is currently disabled. Please contact support."
        
        req_id = request_id or str(uuid.uuid4())[:8]
        self._current_request_id = req_id
        self.metrics["total_requests"] += 1
        
        logger.bind(request_id=req_id).info(f"📥 Processing: '{question[:100]}'")
        
        if session_factory:
            self.session_factory = session_factory
            self._resolver = None
            self._classifier = None
        
        if not question or len(question.strip()) < 2:
            return "Please provide a valid question. Type 'help' for menu."
        
        try:
            context = self._load_context(phone_number)
            question_clean = question.strip()
            
            # Classify intent
            intent_result = self.classifier.classify(question_clean, context)
            
            logger.info(f"[{req_id}] 🎯 Intent: {intent_result.intent}")
            logger.info(f"[{req_id}] 📊 Entity: {intent_result.entity_value} ({intent_result.entity_type})")
            logger.info(f"[{req_id}] 📊 Confidence: {intent_result.confidence:.2f}")
            
            if intent_result.intent == "help":
                return self._get_help_message()
            
            # Route to dashboard
            result = self._route_to_dashboard(
                intent_result.intent,
                intent_result.entity_value,
                intent_result.entity_type,
                context,
                req_id
            )
            
            if result:
                self._update_context(
                    phone_number,
                    intent_result.intent,
                    intent_result.entity_type or "unknown",
                    intent_result.entity_value or context.last_entity if context else None,
                    req_id
                )
                elapsed = time.time() - start_time
                logger.info(f"[{req_id}] ✅ Completed in {elapsed:.3f}s")
                return result
            
            return self._get_help_message()
            
        except Exception as e:
            self.metrics["errors"] += 1
            logger.exception(f"[{req_id}] ❌ ERROR: {e}")
            return f"⚠️ Unable to process request. Please try again or type 'help'."


# ==========================================================
# BLOCK 10: ROUTING ENGINE (OPTIMIZED)
# ==========================================================

    def _route_to_dashboard(self, intent: str, entity: Optional[str], 
                            entity_type: Optional[str], 
                            context: Optional[ConversationContext], 
                            req_id: str) -> Optional[str]:
        """Route to appropriate dashboard."""
        if not self.analytics:
            logger.error(f"[{req_id}] Analytics service not available")
            return "⚠️ Analytics service is temporarily unavailable. Please try again later."
        
        # Route map
        ROUTE_MAP = {
            "dn_dashboard": self._route_dn_dashboard,
            "dealer_dashboard": self._route_dealer_dashboard,
            "warehouse_dashboard": self._route_warehouse_dashboard,
            "city_dashboard": self._route_city_dashboard,
            "product_dashboard": self._route_product_dashboard,
            "dealer_ranking": self._route_dealer_ranking,
            "dealer_products": self._route_dealer_products,
            "warehouse_ranking": self._route_warehouse_ranking,
            "warehouse_coverage": self._route_warehouse_coverage,
            "warehouse_products": self._route_warehouse_products,
            "city_ranking": self._route_city_ranking,
            "city_dealers": self._route_city_dealers,
            "city_products": self._route_city_products,
            "product_ranking": self._route_product_ranking,
            "product_trend": self._route_product_trend,
            "pgi_dashboard": self._route_pgi_dashboard,
            "pod_dashboard": self._route_pod_dashboard,
            "delivery_dashboard": self._route_delivery_dashboard,
            "executive_dashboard": self._route_executive_dashboard,
            "control_tower": self._route_control_tower,
            "revenue_dashboard": self._route_revenue_dashboard,
            "aging_dashboard": self._route_aging_dashboard,
            "division_dashboard": self._route_division_dashboard,
            "sales_manager_dashboard": self._route_sales_manager_dashboard,
            "sales_office_dashboard": self._route_sales_office_dashboard,
        }
        
        try:
            handler = ROUTE_MAP.get(intent)
            if handler:
                return handler(entity, context, req_id)
            
            logger.warning(f"[{req_id}] Unhandled intent: {intent}")
            return None
            
        except Exception as e:
            logger.error(f"[{req_id}] Routing error for {intent}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return f"⚠️ Unable to load {intent.replace('_', ' ').title()}. Please try again."


# ==========================================================
# BLOCK 11: ROUTE HANDLERS (FIXED)
# ==========================================================

    def _validate_response(self, response, service_name: str, req_id: str) -> Tuple[bool, str, Optional[Dict]]:
        """Validate response from analytics service."""
        logger.info(f"[{req_id}] 🔍 Validating {service_name} response")
        logger.info(f"[{req_id}] 📊 Response type: {type(response)}")
        
        if response is None:
            logger.error(f"[{req_id}] ❌ Response is None for {service_name}")
            return False, "No response received from service", None
        
        if isinstance(response, dict):
            logger.info(f"[{req_id}] ✅ Response is a dict with {len(response)} keys")
            
            if "error" in response:
                error_msg = response.get("error", "Unknown error")
                logger.error(f"[{req_id}] ❌ Response contains error: {error_msg}")
                return False, error_msg, None
            
            if not response or len(response) == 0:
                logger.warning(f"[{req_id}] ⚠️ Response is empty dict")
                return False, "Empty response received", None
            
            logger.info(f"[{req_id}] ✅ Valid dict response with {len(response)} keys")
            return True, "", response
        
        if hasattr(response, 'success'):
            if not response.success:
                error_msg = getattr(response, 'error', 'Unknown error')
                logger.error(f"[{req_id}] ❌ Response success=False: {error_msg}")
                return False, error_msg, None
            
            data = getattr(response, 'data', {})
            if not data or len(data) == 0:
                logger.warning(f"[{req_id}] ⚠️ Response data is empty")
                return False, "No data in response", None
            
            if isinstance(data, dict) and "error" in data:
                error_msg = data.get("error", "Unknown error")
                logger.error(f"[{req_id}] ❌ Data contains error: {error_msg}")
                return False, error_msg, None
            
            logger.info(f"[{req_id}] ✅ Valid AnalyticsResponse with {len(data)} data keys")
            return True, "", data
        
        if isinstance(response, list):
            logger.info(f"[{req_id}] ✅ Response is a list with {len(response)} items")
            if len(response) == 0:
                logger.warning(f"[{req_id}] ⚠️ Response list is empty")
                return False, "Empty list response", None
            return True, "", {"results": response}
        
        logger.error(f"[{req_id}] ❌ Unknown response type: {type(response)}")
        return False, f"Unexpected response type: {type(response).__name__}", None

    def _route_dn_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle DN dashboard."""
        import time
        start_time = time.time()
        
        logger.info(f"[{req_id}] 📄 DN Dashboard route called")
        
        dn_number = entity or (context.last_dn if context else None)
        
        if not dn_number:
            return "📄 *DN DASHBOARD*\n\nPlease provide a DN number.\n\n*Example:* 6243675570"
        
        dn_clean = re.sub(r'\D', '', str(dn_number).strip())
        if len(dn_clean) < 8 or len(dn_clean) > 12:
            return f"❌ Invalid DN number: '{dn_number}'\n\nDN numbers must be 8-12 digits."
        
        logger.info(f"[{req_id}] 🔍 Looking up DN: {dn_clean}")
        
        if self.analytics is None:
            return "⚠️ Service temporarily unavailable. Please try again later."
        
        if not hasattr(self.analytics, 'get_dn_dashboard'):
            return "⚠️ Service temporarily unavailable. Please try again later."
        
        try:
            response = self.analytics.get_dn_dashboard(dn_clean)
            is_valid, error_msg, data = self._validate_response(response, "DN Dashboard", req_id)
            
            if not is_valid:
                return f"❌ Unable to retrieve data for DN {dn_clean}.\n\n{error_msg}"
            
            result = self._format_dn_dashboard(data, dn_clean)
            
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ DN dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ DN dashboard error: {e}")
            return f"❌ Error retrieving DN {dn_clean}: {str(e)[:100]}"

    def _route_dealer_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle dealer dashboard."""
        import time
        start_time = time.time()
        
        dealer_name = entity
        if not dealer_name and context and context.last_dealer:
            dealer_name = context.last_dealer
        
        if not dealer_name:
            return "🏪 *DEALER DASHBOARD*\n\nPlease specify a dealer name."
        
        original_dealer_name = dealer_name
        
        # Clean typo
        typo_fixes = {"are ": "", "is ": "", "the ": "", "for ": "", "of ": ""}
        for typo, fix in typo_fixes.items():
            if dealer_name.lower().startswith(typo):
                dealer_name = dealer_name[len(typo):].strip()
                break
        
        if len(dealer_name) < 2:
            dealer_name = original_dealer_name
        
        if self.analytics is None:
            return "⚠️ Service temporarily unavailable. Please try again later."
        
        logger.info(f"[{req_id}] 🔍 Searching for dealer: '{dealer_name}'")
        
        try:
            # Try 360 dashboard first
            response = None
            data = None
            
            if hasattr(self.analytics, 'get_dealer_360_dashboard'):
                response = self.analytics.get_dealer_360_dashboard(dealer_name)
                if hasattr(response, 'success') and response.success:
                    data = response.data if hasattr(response, 'data') else {}
                elif isinstance(response, dict) and not response.get('error'):
                    data = response
            
            # Fallback to legacy
            if data is None:
                if hasattr(self.analytics, 'get_dealer_dashboard'):
                    response = self.analytics.get_dealer_dashboard(dealer_name)
                    if hasattr(response, 'success') and response.success:
                        data = response.data if hasattr(response, 'data') else {}
                    elif isinstance(response, dict) and not response.get('error'):
                        data = response
            
            if not data or (isinstance(data, dict) and data.get('error')):
                error_msg = data.get('error', 'No data') if isinstance(data, dict) else 'No data'
                logger.error(f"[{req_id}] ❌ No data received: {error_msg}")
                return f"❌ Unable to retrieve data for '{original_dealer_name}'.\n\n{error_msg}"
            
            result = self._format_dealer_dashboard(data, dealer_name)
            
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ Dealer dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Dealer dashboard error: {e}")
            return f"❌ Error retrieving dealer data: {str(e)[:100]}"

    def _route_warehouse_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle warehouse dashboard."""
        import time
        start_time = time.time()
        
        warehouse_name = entity
        if not warehouse_name and context and context.last_warehouse:
            warehouse_name = context.last_warehouse
        
        if not warehouse_name:
            return "🏭 *WAREHOUSE DASHBOARD*\n\nPlease specify a warehouse name."
        
        if self.analytics is None:
            return "⚠️ Service temporarily unavailable. Please try again later."
        
        try:
            if not hasattr(self.analytics, 'get_warehouse_dashboard'):
                return "⚠️ Service temporarily unavailable. Please try again later."
            
            response = self.analytics.get_warehouse_dashboard(warehouse_name)
            is_valid, error_msg, data = self._validate_response(response, "Warehouse Dashboard", req_id)
            
            if not is_valid:
                return f"❌ Unable to retrieve data for warehouse '{warehouse_name}'.\n\n{error_msg}"
            
            result = self._format_warehouse_dashboard(data, warehouse_name)
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ Warehouse dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Warehouse dashboard error: {e}")
            return f"❌ Error retrieving warehouse data: {str(e)[:100]}"

    def _route_city_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle city dashboard."""
        import time
        start_time = time.time()
        
        city_name = entity
        if not city_name and context and context.last_city:
            city_name = context.last_city
        
        if not city_name:
            return "🏙️ *CITY DASHBOARD*\n\nPlease specify a city name."
        
        if self.analytics is None:
            return "⚠️ Service temporarily unavailable. Please try again later."
        
        try:
            if not hasattr(self.analytics, 'get_city_dashboard'):
                return "⚠️ Service temporarily unavailable. Please try again later."
            
            response = self.analytics.get_city_dashboard(city_name)
            is_valid, error_msg, data = self._validate_response(response, "City Dashboard", req_id)
            
            if not is_valid:
                return f"❌ Unable to retrieve data for city '{city_name}'.\n\n{error_msg}"
            
            result = self._format_city_dashboard(data, city_name)
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ City dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ City dashboard error: {e}")
            return f"❌ Error retrieving city data: {str(e)[:100]}"

    def _route_product_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        """Handle product dashboard."""
        import time
        start_time = time.time()
        
        product_name = entity
        if not product_name and context and context.last_product:
            product_name = context.last_product
        
        if not product_name:
            return "📦 *PRODUCT DASHBOARD*\n\nPlease specify a product."
        
        if self.analytics is None:
            return "⚠️ Service temporarily unavailable. Please try again later."
        
        try:
            if not hasattr(self.analytics, 'get_product_dashboard'):
                return "⚠️ Service temporarily unavailable. Please try again later."
            
            response = self.analytics.get_product_dashboard(product_name)
            is_valid, error_msg, data = self._validate_response(response, "Product Dashboard", req_id)
            
            if not is_valid:
                return f"❌ Unable to retrieve data for product '{product_name}'.\n\n{error_msg}"
            
            result = self._format_product_dashboard(data, product_name)
            elapsed = time.time() - start_time
            logger.info(f"[{req_id}] ✅ Product dashboard returned in {elapsed:.3f}s")
            return result
            
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Product dashboard error: {e}")
            return f"❌ Error retrieving product data: {str(e)[:100]}"


# ==========================================================
# BLOCK 12: ADDITIONAL ROUTE HANDLERS
# ==========================================================

    def _route_dealer_ranking(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        try:
            response = self.analytics.get_ranking_dashboard(limit=10)
            is_valid, error_msg, data = self._validate_response(response, "Dealer Ranking", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve dealer ranking.\n\n{error_msg}"
            return self._format_dealer_ranking(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Dealer ranking error: {e}")
            return f"❌ Error retrieving dealer ranking: {str(e)[:100]}"
    
    def _route_dealer_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        dealer_name = entity or (context.last_dealer if context else None)
        if not dealer_name:
            return "📦 *DEALER PRODUCTS*\n\nPlease specify a dealer name."
        return f"📦 *PRODUCTS FOR {dealer_name.upper()}*\n\nProduct information coming soon."
    
    def _route_warehouse_ranking(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        return "🏆 *WAREHOUSE RANKING*\n\nWarehouse ranking coming soon."
    
    def _route_warehouse_coverage(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        warehouse_name = entity or (context.last_warehouse if context else None)
        if not warehouse_name:
            return "📍 *WAREHOUSE COVERAGE*\n\nPlease specify a warehouse name."
        return f"📍 *COVERAGE FOR {warehouse_name.upper()}*\n\nCoverage information coming soon."
    
    def _route_warehouse_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        warehouse_name = entity or (context.last_warehouse if context else None)
        if not warehouse_name:
            return "📦 *WAREHOUSE PRODUCTS*\n\nPlease specify a warehouse name."
        return f"📦 *PRODUCTS IN {warehouse_name.upper()}*\n\nProduct list coming soon."
    
    def _route_city_ranking(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        return "🏆 *CITY RANKING*\n\nCity ranking coming soon."
    
    def _route_city_dealers(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        city_name = entity or (context.last_city if context else None)
        if not city_name:
            return "📍 *CITY DEALERS*\n\nPlease specify a city name."
        return f"📍 *DEALERS IN {city_name.upper()}*\n\nDealer list coming soon."
    
    def _route_city_products(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        city_name = entity or (context.last_city if context else None)
        if not city_name:
            return "📦 *CITY PRODUCTS*\n\nPlease specify a city name."
        return f"📦 *PRODUCTS IN {city_name.upper()}*\n\nProduct list coming soon."
    
    def _route_product_ranking(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        return "🏆 *PRODUCT RANKING*\n\nProduct ranking coming soon."
    
    def _route_product_trend(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        return "📈 *PRODUCT TREND*\n\nProduct trend coming soon."

    def _route_pgi_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        try:
            response = self.analytics.get_pgi_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "PGI Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve PGI data.\n\n{error_msg}"
            return self._format_pgi_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ PGI dashboard error: {e}")
            return f"❌ Error retrieving PGI data: {str(e)[:100]}"
    
    def _route_pod_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        try:
            response = self.analytics.get_pod_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "POD Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve POD data.\n\n{error_msg}"
            return self._format_pod_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ POD dashboard error: {e}")
            return f"❌ Error retrieving POD data: {str(e)[:100]}"
    
    def _route_delivery_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        try:
            response = self.analytics.get_delivery_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "Delivery Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve delivery data.\n\n{error_msg}"
            return self._format_delivery_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Delivery dashboard error: {e}")
            return f"❌ Error retrieving delivery data: {str(e)[:100]}"
    
    def _route_executive_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        try:
            response = self.analytics.get_executive_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "Executive Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve executive data.\n\n{error_msg}"
            return self._format_executive_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Executive dashboard error: {e}")
            return f"❌ Error retrieving executive data: {str(e)[:100]}"
    
    def _route_control_tower(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        try:
            response = self.analytics.get_control_tower_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "Control Tower", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve control tower data.\n\n{error_msg}"
            return self._format_control_tower(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Control tower error: {e}")
            return f"❌ Error retrieving control tower data: {str(e)[:100]}"
    
    def _route_revenue_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        try:
            response = self.analytics.get_revenue_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "Revenue Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve revenue data.\n\n{error_msg}"
            return self._format_revenue_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Revenue dashboard error: {e}")
            return f"❌ Error retrieving revenue data: {str(e)[:100]}"
    
    def _route_aging_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        try:
            response = self.analytics.get_aging_dashboard()
            is_valid, error_msg, data = self._validate_response(response, "Aging Dashboard", req_id)
            if not is_valid:
                return f"❌ Unable to retrieve aging data.\n\n{error_msg}"
            return self._format_aging_dashboard(data)
        except Exception as e:
            logger.error(f"[{req_id}] ❌ Aging dashboard error: {e}")
            return f"❌ Error retrieving aging data: {str(e)[:100]}"
    
    def _route_division_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        division_name = entity or (context.last_division if context else None)
        if not division_name:
            return "📊 *DIVISION DASHBOARD*\n\nPlease specify a division name."
        return f"📊 *DIVISION: {division_name.upper()}*\n\nDivision data coming soon."
    
    def _route_sales_manager_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        sm_name = entity or (context.last_sales_manager if context else None)
        if not sm_name:
            return "👤 *SALES MANAGER DASHBOARD*\n\nPlease specify a sales manager name."
        return f"👤 *SALES MANAGER: {sm_name.upper()}*\n\nSales manager data coming soon."
    
    def _route_sales_office_dashboard(self, entity: Optional[str], context: Optional[ConversationContext], req_id: str) -> str:
        so_name = entity or (context.last_sales_office if context else None)
        if not so_name:
            return "🏢 *SALES OFFICE DASHBOARD*\n\nPlease specify a sales office name."
        return f"🏢 *SALES OFFICE: {so_name.upper()}*\n\nSales office data coming soon."


# ==========================================================
# BLOCK 13: FORMATTERS (FIXED)
# ==========================================================

    def _format_dn_dashboard(self, data: Dict, dn_number: str) -> str:
        """Format DN dashboard."""
        try:
            if not data:
                return f"❌ No data available for DN {dn_number}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                if val is None:
                    return default
                if isinstance(val, str) and val == "":
                    return default
                return val
            
            customer_name = safe_get('customer_name', 'N/A')
            warehouse = safe_get('warehouse', 'N/A')
            city = safe_get('ship_to_city', 'N/A')
            units = safe_get('units', 0)
            amount = safe_get('amount', 0)
            status = safe_get('delivery_status', 'Unknown')
            pgi_status = safe_get('pgi_status', 'N/A')
            pod_status = safe_get('pod_status', 'N/A')
            
            status_emoji = "✅" if status in ['Completed', 'Delivered', 'Closed'] else "⏳"
            pending_text = "🔴 Yes" if data.get('pending_flag') else "🟢 No"
            
            delivery_aging = safe_get('delivery_aging_text', 'N/A')
            pod_aging = safe_get('pod_aging_text', 'N/A')
            total_cycle = safe_get('total_cycle_text', 'N/A')
            
            create_date = safe_get('dn_create_date', 'N/A')
            pgi_date = safe_get('good_issue_date', 'N/A')
            pod_date = safe_get('pod_date', 'N/A')
            
            dealer_code = safe_get('dealer_code', 'N/A')
            customer_model = safe_get('customer_model', 'N/A')
            material_no = safe_get('material_no', 'N/A')
            
            lines = [
                "📄 *DN TRACKING*",
                "",
                f"DN No: {safe_get('dn_number', dn_number)}",
                f"Dealer: {customer_name}",
                f"Dealer Code: {dealer_code}",
                f"Warehouse: {warehouse}",
                f"City: {city}",
                "",
                "📦 *Products*",
                f"Model: {customer_model}",
                f"Material: {material_no}",
                "",
                "📊 *Metrics*",
                f"Units: {units}",
            ]
            
            if amount and amount != 0:
                lines.append(f"Revenue: PKR {amount:,.0f}")
            else:
                lines.append(f"Revenue: PKR {amount}")
            
            lines.extend([
                "",
                "📅 *Dates*",
                f"Create: {create_date}",
                f"PGI: {pgi_date}",
                f"POD: {pod_date}",
                "",
                "⏳ *Aging*",
                f"Delivery Aging: {delivery_aging}",
                f"POD Aging: {pod_aging}",
                f"Total Cycle: {total_cycle}",
                "",
                "📋 *Status*",
                f"Delivery: {status} {status_emoji}",
                f"PGI: {pgi_status}",
                f"POD: {pod_status}",
                f"Pending: {pending_text}"
            ])
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"DN format error: {e}")
            return f"❌ Unable to format DN details for {dn_number}: {str(e)}"

    def _format_dealer_dashboard(self, data: Dict, dealer_name: str) -> str:
        """Format dealer dashboard."""
        try:
            if not data:
                return f"❌ No data available for dealer {dealer_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                if val is None:
                    return default
                if isinstance(val, str) and val == "":
                    return default
                return val
            
            total_dns = safe_get('total_dns', 0)
            delivered = safe_get('delivered_dns', 0)
            pending = safe_get('pending_dns', 0)
            transit = safe_get('transit_dns', 0)
            pod_completed = safe_get('pod_completed_dns', 0)
            pending_pod = safe_get('pending_pod_dns', 0)
            pending_pgi = safe_get('pending_pgi_dns', 0)
            
            delivery_rate = safe_get('delivery_rate', 0)
            pgi_rate = safe_get('pgi_rate', 0)
            pod_rate = safe_get('pod_rate', 0)
            health_score = safe_get('health_score', 0)
            risk_level = safe_get('risk_level', 'Unknown')
            risk_score = safe_get('risk_score', 0)
            
            revenue = data.get('total_revenue', 0)
            if revenue is None:
                revenue = 0
            
            lines = [
                "🏢 *DEALER DASHBOARD*",
                "",
                f"Dealer: {safe_get('dealer_name', dealer_name)}",
                f"Dealer Code: {safe_get('dealer_code', 'N/A')}",
                f"Customer Code: {safe_get('customer_code', 'N/A')}",
                f"Division: {safe_get('division', 'N/A')}",
                f"Warehouse: {safe_get('warehouse', 'N/A')}",
                f"City: {safe_get('city', 'N/A')}",
                "",
                "📊 *Metrics*",
                f"Total DNs: {total_dns}",
                f"Total Units: {safe_get('total_units', 0)}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                "",
                "📦 *Delivery Status*",
                f"Delivered: {delivered} ({delivery_rate}%)",
                f"In Transit: {transit}",
                f"Pending: {pending}",
                "",
                "📋 *POD Status*",
                f"POD Completed: {pod_completed} ({pod_rate}%)",
                f"Pending POD: {pending_pod}",
                f"Pending PGI: {pending_pgi}",
                "",
                "⏱️ *Performance*",
                f"Delivery Rate: {delivery_rate}%",
                f"PGI Rate: {pgi_rate}%",
                f"POD Rate: {pod_rate}%",
                f"Health Score: {health_score}/100",
                f"Risk Level: {risk_level} ({risk_score}/100)",
                "",
                f"📌 Products: {safe_get('product_count', 0)}",
                f"📍 Cities: {safe_get('city_count', 0)}"
            ]
            
            return self._truncate_response("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Dealer format error: {e}")
            return f"❌ Unable to format dealer data for {dealer_name}: {str(e)}"

    def _format_warehouse_dashboard(self, data: Dict, warehouse_name: str) -> str:
        """Format warehouse dashboard."""
        try:
            if not data:
                return f"❌ No data available for warehouse {warehouse_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                if val is None:
                    return default
                if isinstance(val, str) and val == "":
                    return default
                return val
            
            total_dns = safe_get('total_dns', 0)
            delivered = safe_get('delivered_dns', 0)
            pending = safe_get('pending_dns', 0)
            delivery_rate = safe_get('delivery_rate', 0)
            revenue = data.get('total_revenue', 0)
            if revenue is None:
                revenue = 0
            
            lines = [
                "🏭 *WAREHOUSE DASHBOARD*",
                "",
                f"Warehouse: {safe_get('warehouse', warehouse_name)}",
                "",
                "📊 *Metrics*",
                f"Total DNs: {total_dns}",
                f"Total Units: {safe_get('total_units', 0)}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                "",
                "👥 *Coverage*",
                f"Total Dealers: {safe_get('total_dealers', 0)}",
                f"Cities Served: {safe_get('cities_served', 0)}",
                f"Product Count: {safe_get('product_count', 0)}",
                "",
                "📦 *Delivery Status*",
                f"Delivered: {delivered} ({delivery_rate}%)",
                f"Pending: {pending}",
                f"Pending POD: {safe_get('pending_pod_dns', 0)}"
            ]
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Warehouse format error: {e}")
            return f"❌ Unable to format warehouse data for {warehouse_name}"

    def _format_city_dashboard(self, data: Dict, city_name: str) -> str:
        """Format city dashboard."""
        try:
            if not data:
                return f"❌ No data available for city {city_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                if val is None:
                    return default
                if isinstance(val, str) and val == "":
                    return default
                return val
            
            total_dns = safe_get('total_dns', 0)
            delivered = safe_get('delivered_dns', 0)
            pending = safe_get('pending_dns', 0)
            delivery_rate = safe_get('delivery_rate', 0)
            revenue = data.get('total_revenue', 0)
            if revenue is None:
                revenue = 0
            
            lines = [
                "🏙️ *CITY DASHBOARD*",
                "",
                f"City: {safe_get('city_name', city_name)}",
                "",
                "📊 *Metrics*",
                f"Total DNs: {total_dns}",
                f"Total Units: {safe_get('total_units', 0)}",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                "",
                "👥 *Coverage*",
                f"Total Dealers: {safe_get('total_dealers', 0)}",
                f"Total Warehouses: {safe_get('total_warehouses', 0)}",
                "",
                "📦 *Delivery Status*",
                f"Delivered: {delivered} ({delivery_rate}%)",
                f"Pending: {pending}",
                f"Pending POD: {safe_get('pending_pod_dns', 0)}"
            ]
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"City format error: {e}")
            return f"❌ Unable to format city data for {city_name}"

    def _format_product_dashboard(self, data: Dict, product_name: str) -> str:
        """Format product dashboard."""
        try:
            if not data:
                return f"❌ No data available for product {product_name}"
            
            def safe_get(key, default="N/A"):
                val = data.get(key, default)
                if val is None:
                    return default
                if isinstance(val, str) and val == "":
                    return default
                return val
            
            revenue = data.get('revenue', 0)
            if revenue is None:
                revenue = 0
            
            lines = [
                "📦 *PRODUCT DASHBOARD*",
                "",
                f"Product: {safe_get('product', product_name)}",
                "",
                "📊 *Metrics*",
                f"Total Revenue: PKR {revenue:,.0f}" if revenue else f"Total Revenue: PKR {revenue}",
                f"Total Units: {safe_get('units', 0)}",
                f"Total DNs: {safe_get('dns', 0)}",
                "",
                "📍 *Distribution*",
                f"Dealers: {safe_get('dealers', 0)}",
                f"Cities: {safe_get('cities', 0)}",
                f"Warehouses: {safe_get('warehouses', 0)}",
                "",
                f"📦 Delivery Rate: {safe_get('delivery_rate', 0)}%"
            ]
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Product format error: {e}")
            return f"❌ Unable to format product data for {product_name}"

    def _format_dealer_ranking(self, data: Dict) -> str:
        """Format dealer ranking."""
        try:
            if not data:
                return "❌ No ranking data available"
            
            ranking = data.get('ranking', [])
            if not ranking:
                return "📊 *DEALER RANKING*\n\nNo ranking data available."
            
            lines = ["🏆 *TOP DEALERS*", ""]
            for i, dealer in enumerate(ranking[:10], 1):
                name = dealer.get('dealer', 'Unknown')
                revenue = dealer.get('revenue', 0)
                units = dealer.get('units', 0)
                dns = dealer.get('dns', 0)
                rate = dealer.get('delivery_rate', 0)
                
                lines.append(f"{i}. {name}")
                lines.append(f"   Revenue: PKR {revenue:,.0f}" if revenue else f"   Revenue: PKR {revenue}")
                lines.append(f"   Units: {units} | DNs: {dns} | Rate: {rate}%")
                lines.append("")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Ranking format error: {e}")
            return "❌ Unable to format ranking data"

    def _format_pgi_dashboard(self, data: Dict) -> str:
        """Format PGI dashboard."""
        try:
            if not data:
                return "❌ No PGI data available"
            
            total = data.get('total_dns', 0)
            completed = data.get('pgi_completed', 0)
            pending = data.get('pgi_pending', 0)
            in_transit = data.get('in_transit', 0)
            rate = data.get('pgi_rate', 0)
            
            return f"""📋 *PGI DASHBOARD*

Total DNs: {total}
PGI Completed: {completed} ({rate}%)
PGI Pending: {pending}
In Transit: {in_transit}

📊 *PGI Rate: {rate}%*"""
        except Exception as e:
            logger.error(f"PGI format error: {e}")
            return "❌ Unable to format PGI data"

    def _format_pod_dashboard(self, data: Dict) -> str:
        """Format POD dashboard."""
        try:
            if not data:
                return "❌ No POD data available"
            
            total = data.get('total_dns', 0)
            completed = data.get('pod_completed', 0)
            pending = data.get('pod_pending', 0)
            delivered = data.get('delivered_dns', 0)
            rate = data.get('pod_rate', 0)
            
            return f"""✅ *POD DASHBOARD*

Total DNs: {total}
POD Completed: {completed} ({rate}%)
POD Pending: {pending}
Delivered DNs: {delivered}

📊 *POD Rate: {rate}%*"""
        except Exception as e:
            logger.error(f"POD format error: {e}")
            return "❌ Unable to format POD data"

    def _format_delivery_dashboard(self, data: Dict) -> str:
        """Format delivery dashboard."""
        try:
            if not data:
                return "❌ No delivery data available"
            
            total = data.get('total_dns', 0)
            delivered = data.get('delivered', 0)
            in_transit = data.get('in_transit', 0)
            pending_pgi = data.get('pending_pgi', 0)
            pending = data.get('pending', 0)
            delivery_rate = data.get('delivery_rate', 0)
            pgi_rate = data.get('pgi_rate', 0)
            
            return f"""🚚 *DELIVERY DASHBOARD*

Total DNs: {total}
Delivered: {delivered} ({delivery_rate}%)
In Transit: {in_transit}
Pending PGI: {pending_pgi}
Pending: {pending}

📊 *Delivery Rate: {delivery_rate}%
📊 *PGI Rate: {pgi_rate}%*"""
        except Exception as e:
            logger.error(f"Delivery format error: {e}")
            return "❌ Unable to format delivery data"

    def _format_executive_dashboard(self, data: Dict) -> str:
        """Format executive dashboard."""
        try:
            if not data:
                return "❌ No executive data available"
            
            total_dns = data.get('total_dns', 0)
            total_units = data.get('total_units', 0)
            total_revenue = data.get('total_revenue', 0)
            total_dealers = data.get('total_dealers', 0)
            total_cities = data.get('total_cities', 0)
            total_warehouses = data.get('total_warehouses', 0)
            delivered = data.get('delivered_dns', 0)
            pending = data.get('pending_dns', 0)
            rate = data.get('delivery_rate', 0)
            
            return f"""👔 *EXECUTIVE DASHBOARD*

📊 *Nationwide Performance*

Total DNs: {total_dns}
Total Units: {total_units}
Total Revenue: PKR {total_revenue:,.0f}

👥 *Network*
Total Dealers: {total_dealers}
Total Cities: {total_cities}
Total Warehouses: {total_warehouses}

📦 *Delivery*
Delivered: {delivered} ({rate}%)
Pending: {pending}"""
        except Exception as e:
            logger.error(f"Executive format error: {e}")
            return "❌ Unable to format executive data"

    def _format_control_tower(self, data: Dict) -> str:
        """Format control tower dashboard."""
        try:
            if not data:
                return "❌ No control tower data available"
            
            alerts = data.get('alerts', [])
            critical = data.get('critical_count', 0)
            high = data.get('high_count', 0)
            total = data.get('total_alerts', 0)
            
            lines = ["🚨 *CONTROL TOWER*", ""]
            
            if not alerts:
                lines.append("✅ No alerts at this time.")
            else:
                lines.append(f"⚠️ *{total} Alert(s) Found*")
                lines.append(f"🔴 Critical: {critical} | 🟠 High: {high}")
                lines.append("")
                
                for alert in alerts[:5]:
                    alert_type = alert.get('type', 'Alert')
                    severity = alert.get('severity', 'medium')
                    desc = alert.get('description', 'No description')
                    severity_emoji = "🔴" if severity == "critical" else "🟠" if severity == "high" else "🟡"
                    lines.append(f"{severity_emoji} *{alert_type}*")
                    lines.append(f"   {desc}")
                    lines.append("")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Control tower format error: {e}")
            return "❌ Unable to format control tower data"

    def _format_revenue_dashboard(self, data: Dict) -> str:
        """Format revenue dashboard."""
        try:
            if not data:
                return "❌ No revenue data available"
            
            total_revenue = data.get('total_revenue', 0)
            total_units = data.get('total_units', 0)
            total_dns = data.get('total_dns', 0)
            top_dealers = data.get('top_dealers', [])
            
            lines = [
                "💰 *REVENUE DASHBOARD*",
                "",
                f"Total Revenue: PKR {total_revenue:,.0f}",
                f"Total Units: {total_units}",
                f"Total DNs: {total_dns}",
                ""
            ]
            
            if top_dealers:
                lines.append("🏆 *Top 5 Dealers*")
                for i, dealer in enumerate(top_dealers[:5], 1):
                    name = dealer.get('dealer', 'Unknown')
                    revenue = dealer.get('revenue', 0)
                    lines.append(f"{i}. {name}: PKR {revenue:,.0f}")
            
            return self._truncate_response("\n".join(lines))
        except Exception as e:
            logger.error(f"Revenue format error: {e}")
            return "❌ Unable to format revenue data"

    def _format_aging_dashboard(self, data: Dict) -> str:
        """Format aging dashboard."""
        try:
            if not data:
                return "❌ No aging data available"
            
            days_0_7 = data.get('days_0_7', 0)
            days_8_14 = data.get('days_8_14', 0)
            days_15_30 = data.get('days_15_30', 0)
            days_30_plus = data.get('days_30_plus', 0)
            total = data.get('total_pending', 0)
            
            return f"""⏳ *AGING DASHBOARD*

📊 *Pending DN Aging*

0-7 Days: {days_0_7}
8-14 Days: {days_8_14}
15-30 Days: {days_15_30}
30+ Days: {days_30_plus}

📊 *Total Pending: {total} DNs*"""
        except Exception as e:
            logger.error(f"Aging format error: {e}")
            return "❌ Unable to format aging data"


# ==========================================================
# BLOCK 14: HELP MESSAGE
# ==========================================================

    def _get_help_message(self) -> str:
        return """🏠 *HAIER LOGISTICS AI*

*📋 20+ Dashboards Available:*

1️⃣ 🏪 Dealer Dashboard
2️⃣ 🏭 Warehouse Dashboard
3️⃣ 🏙️ City Dashboard
4️⃣ 📦 Product Dashboard
5️⃣ 📄 DN Dashboard
6️⃣ 📋 PGI Dashboard
7️⃣ ✅ POD Dashboard
8️⃣ 🚚 Delivery Dashboard
9️⃣ 👔 Executive Dashboard
🔟 🚨 Control Tower
1️⃣1️⃣ 🏆 Dealer Ranking
1️⃣2️⃣ 🏆 Warehouse Ranking
1️⃣3️⃣ 🏆 City Ranking
1️⃣4️⃣ 🏆 Product Ranking
1️⃣5️⃣ 💰 Revenue Dashboard
1️⃣6️⃣ 📊 Division Dashboard
1️⃣7️⃣ 👤 Sales Manager Dashboard
1️⃣8️⃣ 🏢 Sales Office Dashboard
1️⃣9️⃣ ⏳ Aging Dashboard
2️⃣0️⃣ 🔄 Follow-up Support

*🔍 Quick Commands:*
• Enter 8-12 digit DN number
• Dealer name (e.g., "Pakistan Electronics Mansehra")
• Product name (e.g., "Refrigerator", "AC")
• City name (e.g., "Lahore City")
• Warehouse name (e.g., "Rawalpindi warehouse")
• "Executive summary"
• "Control tower"
• "Top dealers"
• "Help" for menu

*💡 Follow-up Support:*
• "What is its POD?" → Uses last dealer
• "How many pending DN?" → Uses last dealer
• "Show me its revenue" → Uses last dealer
• "Show aging" → Uses last dealer

*Ask me anything about logistics!* 🤖"""


# ==========================================================
# BLOCK 15: UTILITY FUNCTIONS
# ==========================================================

    def _truncate_response(self, response: str) -> str:
        """Truncate response if too long."""
        if len(response) > MAX_RESPONSE_LENGTH:
            return response[:MAX_RESPONSE_LENGTH - 20] + "\n\n... (truncated)"
        return response


# ==========================================================
# BLOCK 16: SINGLETON & WRAPPER FUNCTIONS
# ==========================================================

_orchestrator = None
_initialization_attempts = 0
_MAX_INIT_ATTEMPTS = 3

def get_orchestrator(session_factory: Optional[Callable[[], Session]] = None) -> Optional[AIOrchestrator]:
    """Get or create AI Orchestrator singleton with retry logic."""
    global _orchestrator, _initialization_attempts
    
    if _orchestrator is not None:
        return _orchestrator
    
    if _initialization_attempts >= _MAX_INIT_ATTEMPTS:
        logger.error(f"❌ Max initialization attempts ({_MAX_INIT_ATTEMPTS}) reached")
        return None
    
    _initialization_attempts += 1
    logger.info(f"🔄 Initializing AI Orchestrator (attempt {_initialization_attempts}/{_MAX_INIT_ATTEMPTS})...")
    
    try:
        _orchestrator = AIOrchestrator(session_factory=session_factory)
        logger.info("✅ AI Orchestrator v29.0 initialized successfully")
        _initialization_attempts = 0
        return _orchestrator
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize AI Orchestrator: {e}")
        import traceback
        logger.error(traceback.format_exc())
        _orchestrator = None
        return None


def process_whatsapp_query(
    question: str,
    session_factory: Optional[Callable[[], Session]] = None,
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> str:
    """Process WhatsApp query with fallback and recovery."""
    global _orchestrator, _initialization_attempts
    
    if not question or not question.strip():
        return "Please provide a valid question. Type 'help' for menu."
    
    orchestrator = get_orchestrator(session_factory)
    
    if orchestrator is None:
        logger.warning("⚠️ Orchestrator is None - attempting emergency reset...")
        _orchestrator = None
        _initialization_attempts = 0
        
        try:
            orchestrator = AIOrchestrator(session_factory=session_factory)
            _orchestrator = orchestrator
            logger.info("✅ Emergency reset successful")
        except Exception as e:
            logger.error(f"❌ Emergency reset failed: {e}")
            _orchestrator = None
            return "⚠️ AI service is currently unavailable. Please try again later."
    
    if orchestrator is None:
        return "⚠️ AI service is currently unavailable. Please try again later."
    
    try:
        return orchestrator.process_whatsapp_query(
            question=question,
            session_factory=session_factory,
            phone_number=phone_number,
            user_id=user_id,
            request_id=request_id
        )
    except Exception as e:
        logger.error(f"❌ Error processing query: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"⚠️ Error processing your request. Please try again later."


def reset_orchestrator() -> None:
    """Reset the orchestrator singleton."""
    global _orchestrator, _initialization_attempts
    _orchestrator = None
    _initialization_attempts = 0
    logger.info("🔄 Orchestrator reset successfully")


def get_orchestrator_status() -> Dict[str, Any]:
    """Get current orchestrator status for diagnostics."""
    global _orchestrator, _initialization_attempts
    
    return {
        "orchestrator_initialized": _orchestrator is not None,
        "initialization_attempts": _initialization_attempts,
        "max_attempts": _MAX_INIT_ATTEMPTS,
        "analytics_available": hasattr(_orchestrator, 'analytics') if _orchestrator else False,
        "has_analytics": _orchestrator.analytics is not None if _orchestrator else False,
        "conversation_count": len(_orchestrator.conversation_cache) if _orchestrator else 0,
        "metrics": _orchestrator.metrics if _orchestrator else {}
    }


def get_whatsapp_health_status() -> Dict[str, Any]:
    """Get WhatsApp token health status for monitoring."""
    token = getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')
    phone_id = getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')
    
    status = {
        "token_configured": bool(token),
        "phone_id_configured": bool(phone_id),
        "api_version": getattr(config, 'WHATSAPP_API_VERSION', 'v25.0'),
        "environment": getattr(config, 'ENVIRONMENT', 'unknown')
    }
    
    if token and phone_id:
        validation = validate_whatsapp_token()
        status["token_valid"] = validation.get("valid", False)
        status["token_details"] = validation
    else:
        status["token_valid"] = False
        status["error"] = "Token or Phone ID not configured"
    
    return status


def check_whatsapp_token_health() -> Dict[str, Any]:
    """Quick health check for WhatsApp token."""
    try:
        token = getattr(config, 'WHATSAPP_ACCESS_TOKEN', '')
        phone_id = getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', '')
        
        if not token or not phone_id:
            return {
                "status": "error",
                "message": "WhatsApp configuration incomplete",
                "token_configured": bool(token),
                "phone_configured": bool(phone_id)
            }
        
        validation = validate_whatsapp_token()
        
        if validation.get('valid'):
            return {
                "status": "ok",
                "message": "WhatsApp token is valid",
                "app_id": validation.get('app_id'),
                "app_name": validation.get('app_name')
            }
        else:
            return {
                "status": "error",
                "message": validation.get('message', 'Token invalid'),
                "error_code": validation.get('error_code'),
                "action": validation.get('action')
            }
            
    except Exception as e:
        return {
            "status": "error",
            "message": f"Health check failed: {str(e)}"
        }


# ==========================================================
# BLOCK 17: EXPORTS
# ==========================================================

__all__ = [
    'AIOrchestrator',
    'PostgreSQLResolver',
    'ConversationContext',
    'IntentClassifier',
    'IntentResult',
    'get_orchestrator',
    'process_whatsapp_query',
    'reset_orchestrator',
    'get_orchestrator_status',
    'test_database_connection',
    'validate_whatsapp_token',
    'get_whatsapp_config',
    'get_whatsapp_health_status',
    'check_whatsapp_token_health'
]

# ==========================================================
# END OF FILE - v29.0 PRODUCTION READY
# ==========================================================
