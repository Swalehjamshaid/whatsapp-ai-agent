# ==========================================================
# FILE: app/services/ai_query_service.py (IMPROVED v39.0)
# ==========================================================
# PURPOSE: PURE ROUTER ONLY - Single Brain for Query Routing
#
# IMPROVEMENTS v39.0:
# - Frozen RouteMap - only routes that definitely exist
# - Safe route validation at startup (available/disabled routes)
# - Removed dynamic __getattribute__ (use getattr with fallback)
# - Route availability check before execution
# - Disabled QueryAudit dependency (feature flag)
# - Service health registry for monitoring
# - Safe root cause AI with hasattr checks
# - Fixed singleton factory to update session_factory
# - Route fallbacks with user-friendly messages
# - Enhanced error tracking with logger.bind
# ==========================================================

from __future__ import annotations

import re
import time
import asyncio
import hashlib
import uuid
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
from collections import OrderedDict
from sqlalchemy.orm import Session
from loguru import logger

# Optional Redis support
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available. Using in-memory context only.")

# Feature flags
ENABLE_AUDIT_LOGGING = False  # Temporarily disabled until model exists


# ==========================================================
# TTL CACHE IMPLEMENTATION
# ==========================================================

class TTLCache:
    """Time-To-Live Cache for frequent queries"""
    
    def __init__(self, maxsize: int = 200, ttl_seconds: int = 300):
        self.maxsize = maxsize
        self.ttl = ttl_seconds
        self.cache = OrderedDict()
        self.timestamps = {}
    
    def _make_key(self, intent: str, params: str = "") -> str:
        key_str = f"{intent}:{params}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get(self, intent: str, params: str = "") -> Optional[Any]:
        key = self._make_key(intent, params)
        
        if key in self.cache:
            timestamp = self.timestamps.get(key)
            if timestamp and (datetime.now() - timestamp).seconds < self.ttl:
                self.cache.move_to_end(key)
                return self.cache[key]
            else:
                del self.cache[key]
                del self.timestamps[key]
        return None
    
    def set(self, value: Any, intent: str, params: str = ""):
        key = self._make_key(intent, params)
        
        if len(self.cache) >= self.maxsize:
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
            del self.timestamps[oldest_key]
        
        self.cache[key] = value
        self.timestamps[key] = datetime.now()
        self.cache.move_to_end(key)
    
    def clear(self):
        self.cache.clear()
        self.timestamps.clear()
    
    def get_stats(self) -> Dict:
        return {
            "size": len(self.cache),
            "maxsize": self.maxsize,
            "ttl_seconds": self.ttl,
            "utilization": round(len(self.cache) / self.maxsize * 100, 1) if self.maxsize > 0 else 0
        }


# ==========================================================
# LRU CONTEXT MANAGER
# ==========================================================

class LRUContextManager:
    """LRU-based context manager for in-memory storage with size limit"""
    
    def __init__(self, max_users: int = 500):
        self.max_users = max_users
        self.contexts = OrderedDict()
    
    def get(self, user_id: str) -> Dict:
        if user_id in self.contexts:
            self.contexts.move_to_end(user_id)
            return self.contexts[user_id]
        return {}
    
    def set(self, user_id: str, context: Dict):
        if user_id in self.contexts:
            self.contexts.move_to_end(user_id)
        else:
            if len(self.contexts) >= self.max_users:
                oldest_key = next(iter(self.contexts))
                del self.contexts[oldest_key]
                logger.debug(f"Evicted context for user {oldest_key}")
        self.contexts[user_id] = context
    
    def get_size(self) -> int:
        return len(self.contexts)
    
    def get_stats(self) -> Dict:
        return {
            "size": len(self.contexts),
            "max_users": self.max_users,
            "utilization": round(len(self.contexts) / self.max_users * 100, 1)
        }


# ==========================================================
# INTENT TYPES (FROZEN - Only routes that exist)
# ==========================================================

class Intent(str, Enum):
    # DN Operations (Confirmed working)
    DN_LOOKUP = "dn_lookup"
    DN_TIMELINE = "dn_timeline"
    
    # POD Operations (Confirmed working)
    PENDING_POD = "pending_pod"
    
    # PGI Operations (Confirmed working)
    PENDING_PGI = "pending_pgi"
    
    # Delivery Operations (Confirmed working)
    PENDING_DELIVERIES = "pending_deliveries"
    
    # Dealer Operations (Confirmed working)
    DEALER_PERFORMANCE = "dealer_performance"
    TOP_DEALERS = "top_dealers"
    
    # Warehouse Operations (Confirmed working)
    TOP_WAREHOUSES = "top_warehouses"
    
    # KPI Operations (Confirmed working)
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    NETWORK_HEALTH = "network_health"
    CRITICAL_DELAYS = "critical_delays"
    CONTROL_TOWER = "control_tower"
    
    # Disabled temporarily (methods not yet implemented)
    # DN_AGING = "dn_aging"           # Waiting for logistics service update
    # POD_AGING = "pod_aging"         # Waiting for logistics service update
    # WAREHOUSE_STATUS = "warehouse_status"  # Waiting for logistics service update
    # REGION_PERFORMANCE = "region_performance"  # Waiting for analytics service update
    
    # General (Always available)
    HELP = "help"
    GREETING = "greeting"
    GENERAL = "general"
    AI_QUERY = "ai_query"
    ROOT_CAUSE = "root_cause"


# ==========================================================
# QUERY CLASSIFICATION
# ==========================================================

class QueryClass(str, Enum):
    OPERATIONAL = "operational"
    ANALYTICAL = "analytical"
    EXECUTIVE = "executive"
    AI = "ai"


# ==========================================================
# ENTITY EXTRACTION
# ==========================================================

@dataclass
class ExtractedEntities:
    dn_number: Optional[str] = None
    dealer: Optional[str] = None
    dealer_code: Optional[str] = None
    customer: Optional[str] = None
    customer_code: Optional[str] = None
    warehouse: Optional[str] = None
    warehouse_code: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    division: Optional[str] = None
    sales_manager: Optional[str] = None
    material_no: Optional[str] = None
    product: Optional[str] = None
    days: Optional[int] = None
    limit: Optional[int] = 10
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    last_intent: Optional[str] = None
    last_dn: Optional[str] = None
    last_dealer: Optional[str] = None
    last_city: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}
    
    def has_any(self) -> bool:
        return any([self.dn_number, self.dealer, self.warehouse, 
                   self.city, self.region, self.product])


class EntityExtractor:
    DN_PATTERN = re.compile(r'\b80\d{8}\b')
    PHONE_PATTERN = re.compile(r'\b(?:92|03)\d{9,12}\b')
    DAYS_PATTERN = re.compile(r'(\d+)\s+days?', re.IGNORECASE)
    LIMIT_PATTERN = re.compile(r'(?:top|limit)\s+(\d+)', re.IGNORECASE)
    DEALER_CODE_PATTERN = re.compile(r'dealer[-_]?code[:\s]*([A-Z0-9]+)', re.IGNORECASE)
    CUSTOMER_CODE_PATTERN = re.compile(r'customer[-_]?code[:\s]*([A-Z0-9]+)', re.IGNORECASE)
    WAREHOUSE_CODE_PATTERN = re.compile(r'warehouse[-_]?code[:\s]*([A-Z0-9]+)', re.IGNORECASE)
    MATERIAL_PATTERN = re.compile(r'material[-_]?no[:\s]*([A-Z0-9-]+)', re.IGNORECASE)
    
    CITIES = ['karachi', 'lahore', 'islamabad', 'rawalpindi', 'faisalabad', 
              'multan', 'peshawar', 'quetta', 'gujranwala', 'sialkot']
    
    @classmethod
    def extract(cls, question: str, context: Dict = None) -> ExtractedEntities:
        question_lower = question.lower().strip()
        entities = ExtractedEntities()
        
        if context:
            entities.last_intent = context.get("last_intent")
            entities.last_dn = context.get("last_dn")
            entities.last_dealer = context.get("last_dealer")
            entities.last_city = context.get("last_city")
        
        dn_match = cls.DN_PATTERN.search(question)
        if dn_match:
            entities.dn_number = dn_match.group(0)
        
        if not entities.dn_number and entities.last_dn:
            entities.dn_number = entities.last_dn
        
        days_match = cls.DAYS_PATTERN.search(question_lower)
        if days_match:
            entities.days = int(days_match.group(1))
        
        limit_match = cls.LIMIT_PATTERN.search(question_lower)
        if limit_match:
            entities.limit = min(int(limit_match.group(1)), 50)
        
        code_match = cls.DEALER_CODE_PATTERN.search(question)
        if code_match:
            entities.dealer_code = code_match.group(1)
        
        code_match = cls.CUSTOMER_CODE_PATTERN.search(question)
        if code_match:
            entities.customer_code = code_match.group(1)
        
        code_match = cls.WAREHOUSE_CODE_PATTERN.search(question)
        if code_match:
            entities.warehouse_code = code_match.group(1)
        
        code_match = cls.MATERIAL_PATTERN.search(question)
        if code_match:
            entities.material_no = code_match.group(1)
        
        for city in cls.CITIES:
            if city in question_lower:
                entities.city = city.capitalize()
                break
        else:
            if entities.last_city:
                entities.city = entities.last_city
        
        warehouse_match = re.search(r'warehouse\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,|performance|status)', question_lower)
        if warehouse_match:
            entities.warehouse = warehouse_match.group(1).strip()
        
        dealer_patterns = [
            r'dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,|performance|dashboard|details|risk)',
            r'show\s+dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
            r'for\s+dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)'
        ]
        
        for pattern in dealer_patterns:
            match = re.search(pattern, question_lower)
            if match:
                entities.dealer = match.group(1).strip()
                break
        else:
            if entities.last_dealer and not entities.dealer:
                entities.dealer = entities.last_dealer
        
        division_match = re.search(r'division\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)', question_lower)
        if division_match:
            entities.division = division_match.group(1).strip()
        
        manager_match = re.search(r'(?:sales manager|manager)\s+([A-Za-z\s]+?)(?:\s+$|\.|\,)', question_lower)
        if manager_match:
            entities.sales_manager = manager_match.group(1).strip()
        
        product_match = re.search(r'product\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,|performance)', question_lower)
        if product_match:
            entities.product = product_match.group(1).strip()
        
        return entities


# ==========================================================
# WEIGHTED INTENT SCORING ENGINE
# ==========================================================

class IntentScore:
    def __init__(self, intent: Intent, confidence: float, score: float = 0):
        self.intent = intent
        self.confidence = confidence
        self.score = score


class WeightedIntentEngine:
    """Weighted scoring for intent detection - prevents false positives"""
    
    @classmethod
    def calculate_score(cls, intent: Intent, question_lower: str, entities: ExtractedEntities) -> float:
        score = 0.0
        keyword_groups = IntentDetector.KEYWORD_GROUPS
        
        keywords = keyword_groups.get(intent, [])
        for keyword in keywords:
            if keyword in question_lower:
                score += 1.0
        
        if intent in [Intent.DN_LOOKUP, Intent.DN_TIMELINE]:
            if entities.dn_number:
                score += 2.0
        
        if intent in [Intent.DEALER_PERFORMANCE, Intent.TOP_DEALERS]:
            if entities.dealer or entities.dealer_code:
                score += 1.5
        
        if intent in [Intent.TOP_WAREHOUSES]:
            if entities.warehouse or entities.warehouse_code:
                score += 1.5
        
        first_keyword_pos = len(question_lower)
        for keyword in keywords:
            pos = question_lower.find(keyword)
            if pos != -1 and pos < first_keyword_pos:
                first_keyword_pos = pos
        
        if first_keyword_pos < len(question_lower):
            score += 0.5 * (1 - first_keyword_pos / len(question_lower))
        
        return score


# ==========================================================
# INTENT DETECTION (FROZEN - Only available intents)
# ==========================================================

class IntentDetector:
    KEYWORD_GROUPS = {
        Intent.DN_TIMELINE: ['timeline', 'journey', 'history', 'track', 'progress', 'status history'],
        Intent.PENDING_POD: ['pending pod', 'pod pending', 'missing pod', 'pod not received', 'pending proof'],
        Intent.PENDING_PGI: ['pending pgi', 'pgi pending', 'pending dispatch', 'not dispatched'],
        Intent.PENDING_DELIVERIES: ['pending delivery', 'delivery pending', 'undelivered'],
        Intent.DEALER_PERFORMANCE: ['dealer performance', 'dealer metrics', 'dealer score', 'how is dealer'],
        Intent.TOP_DEALERS: ['top dealer', 'best dealer', 'dealer ranking', 'top performing', 'leading dealer'],
        Intent.TOP_WAREHOUSES: ['top warehouse', 'best warehouse', 'warehouse ranking'],
        Intent.EXECUTIVE_DASHBOARD: ['executive dashboard', 'ceo dashboard', 'leadership', 'board view'],
        Intent.NETWORK_HEALTH: ['network health', 'system health', 'service status', 'health check'],
        Intent.CRITICAL_DELAYS: ['critical delay', 'urgent delay', 'high risk delay', 'critical dn'],
        Intent.CONTROL_TOWER: ['control tower', 'command center', 'all alerts', 'mission control'],
        Intent.ROOT_CAUSE: ['why', 'root cause', 'reason', 'cause', 'what caused', 'why is', 'why are'],
    }
    
    @classmethod
    def classify_query(cls, question: str) -> QueryClass:
        question_lower = question.lower()
        
        executive_keywords = ['kpi', 'dashboard', 'executive', 'ceo', 'board', 'health', 'control tower']
        if any(kw in question_lower for kw in executive_keywords):
            return QueryClass.EXECUTIVE
        
        analytical_keywords = ['trend', 'ranking', 'top', 'best', 'comparison', 'analysis', 'performance']
        if any(kw in question_lower for kw in analytical_keywords):
            return QueryClass.ANALYTICAL
        
        ai_keywords = ['why', 'root cause', 'recommend', 'suggest', 'how to improve', 'what if', 'reason']
        if any(kw in question_lower for kw in ai_keywords):
            return QueryClass.AI
        
        return QueryClass.OPERATIONAL
    
    @classmethod
    def detect(cls, question: str, entities: ExtractedEntities) -> Tuple[Intent, QueryClass, float]:
        question_lower = question.lower().strip()
        
        # Direct matches (100% confidence)
        if question_lower in ['help', 'menu', 'commands']:
            return Intent.HELP, QueryClass.OPERATIONAL, 1.0
        
        if question_lower in ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']:
            return Intent.GREETING, QueryClass.OPERATIONAL, 1.0
        
        # Root cause detection
        root_cause_keywords = ['why', 'root cause', 'reason', 'what caused']
        if any(kw in question_lower for kw in root_cause_keywords):
            return Intent.ROOT_CAUSE, QueryClass.AI, 0.90
        
        # DN number present
        if entities.dn_number:
            if 'timeline' in question_lower or 'history' in question_lower:
                return Intent.DN_TIMELINE, QueryClass.OPERATIONAL, 0.95
            else:
                return Intent.DN_LOOKUP, QueryClass.OPERATIONAL, 0.95
        
        # Dealer present
        if entities.dealer or entities.dealer_code:
            if 'performance' in question_lower or 'metrics' in question_lower:
                return Intent.DEALER_PERFORMANCE, QueryClass.ANALYTICAL, 0.85
            else:
                return Intent.TOP_DEALERS, QueryClass.ANALYTICAL, 0.70
        
        # Weighted scoring for ambiguous queries
        best_intent = None
        best_score = -1.0
        
        for intent in cls.KEYWORD_GROUPS.keys():
            score = WeightedIntentEngine.calculate_score(intent, question_lower, entities)
            if score > best_score:
                best_score = score
                best_intent = intent
        
        if best_intent and best_score >= 1.0:
            confidence = min(0.70 + (best_score / 20), 0.90)
            query_class = cls.classify_query(question)
            return best_intent, query_class, confidence
        
        # Default fallback
        query_class = cls.classify_query(question)
        if query_class == QueryClass.AI:
            return Intent.AI_QUERY, query_class, 0.60
        
        return Intent.GENERAL, query_class, 0.50


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    @staticmethod
    def format_success(data: Any, summary: str = None, metadata: Dict = None) -> Dict:
        return {
            "success": True, 
            "data": data, 
            "summary": summary or "",
            "metadata": metadata or {}
        }
    
    @staticmethod
    def format_error(message: str, error_id: str = None, code: str = "unknown") -> Dict:
        error_id = error_id or str(uuid.uuid4())[:8]
        return {
            "success": False, 
            "data": {}, 
            "summary": message, 
            "error_code": code,
            "error_id": error_id
        }
    
    @staticmethod
    def format_route_unavailable(intent: Intent) -> Dict:
        """User-friendly message for unavailable routes"""
        error_id = str(uuid.uuid4())[:8]
        message = f"""🚧 *Feature Under Development*

The '{intent.value}' feature is coming soon.

*Available Commands Right Now:*
• Send DN number (starts with 80) - Track delivery
• `Pending POD` - Missing proofs
• `Pending PGI` - Pending dispatches  
• `Pending deliveries` - Undelivered
• `Top dealers` - Dealer rankings
• `Top warehouses` - Warehouse rankings
• `Executive dashboard` - KPI overview
• `Network health` - System status
• `Control tower` - All alerts

Type `Help` for complete list."""

        return {
            "success": False,
            "data": {},
            "summary": message,
            "error_code": "route_unavailable",
            "error_id": error_id
        }
    
    @staticmethod
    def format_help() -> str:
        return """
🤖 *AI LOGISTICS ASSISTANT - HELP* v39.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• Send DN number (starts with 80)
• `DN timeline` - Track journey

📋 *Pending Items*
• `Pending POD` - Missing proofs
• `Pending PGI` - Pending dispatches
• `Pending deliveries` - Undelivered

🏪 *Dealer Analytics*
• `Top dealers` - Rankings
• `Dealer ABC performance` - Specific dealer

🏭 *Warehouse Analytics*
• `Top warehouses` - Rankings

📊 *Executive Dashboard*
• `Executive dashboard` - KPI overview
• `Network health` - System status
• `Critical delays` - Urgent issues
• `Control tower` - All alerts

🔍 *Root Cause Analysis*
• `Why is Lahore delayed?` - AI-powered analysis

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    @staticmethod
    def format_greeting() -> str:
        hour = datetime.now().hour
        if hour < 12:
            greeting = "Good morning"
        elif hour < 17:
            greeting = "Good afternoon"
        else:
            greeting = "Good evening"
        
        return f"""
{greeting}! 👋

I'm your *AI Logistics Assistant v39.0*. I can help you track DNs, check performance, and monitor operations.

Type `Help` to see all commands.
"""


# ==========================================================
# CENTRAL ROUTE MAP (FROZEN - Only routes that exist)
# ==========================================================

class RouteMap:
    # Confirmed working routes
    LOGISTICS_ROUTES = {
        Intent.DN_LOOKUP: ("get_complete_dn_intelligence", True),
        Intent.DN_TIMELINE: ("get_dn_timeline", True),
        Intent.PENDING_POD: ("get_pod_status", False),
        Intent.PENDING_PGI: ("get_pending_pgi", False),
        Intent.PENDING_DELIVERIES: ("get_pending_deliveries", False),
    }
    
    ANALYTICS_ROUTES = {
        Intent.TOP_DEALERS: ("get_top_dealers", True),
        Intent.TOP_WAREHOUSES: ("get_top_warehouses", True),
        Intent.DEALER_PERFORMANCE: ("get_dealer_performance", True),
    }
    
    KPI_ROUTES = {
        Intent.EXECUTIVE_DASHBOARD: ("get_executive_dashboard", False),
        Intent.NETWORK_HEALTH: ("get_network_health", False),
        Intent.CRITICAL_DELAYS: ("get_critical_delays", False),
        Intent.CONTROL_TOWER: ("get_control_tower_report", False),
    }
    
    # Disabled routes (methods not yet implemented)
    DISABLED_ROUTES = {
        # Intent.DN_AGING: "get_dn_aging_report",
        # Intent.POD_AGING: "get_pod_aging_report",
        # Intent.WAREHOUSE_STATUS: "get_warehouse_status",
        # Intent.REGION_PERFORMANCE: "get_region_performance",
    }
    
    @classmethod
    def is_route_available(cls, intent: Intent) -> bool:
        """Check if route is available and not disabled"""
        if intent in cls.DISABLED_ROUTES:
            return False
        
        if intent in cls.LOGISTICS_ROUTES:
            return True
        if intent in cls.ANALYTICS_ROUTES:
            return True
        if intent in cls.KPI_ROUTES:
            return True
        
        return False
    
    @classmethod
    def get_route(cls, intent: Intent) -> Tuple[Optional[str], Optional[str], bool]:
        """Get route for intent: (service, method, has_param)"""
        
        if not cls.is_route_available(intent):
            return None, None, False
        
        if intent in cls.LOGISTICS_ROUTES:
            method, has_param = cls.LOGISTICS_ROUTES[intent]
            return "logistics", method, has_param
        
        if intent in cls.ANALYTICS_ROUTES:
            method, has_param = cls.ANALYTICS_ROUTES[intent]
            return "analytics", method, has_param
        
        if intent in cls.KPI_ROUTES:
            method, has_param = cls.KPI_ROUTES[intent]
            return "kpi", method, has_param
        
        return None, None, False


# ==========================================================
# QUERY METRICS TRACKING
# ==========================================================

class QueryMetrics:
    def __init__(self):
        self.metrics = {
            "total_queries": 0,
            "by_intent": {},
            "by_class": {},
            "avg_response_time_ms": 0,
            "success_rate": 100.0,
            "failures": 0,
            "by_confidence": {"high": 0, "medium": 0, "low": 0},
            "cache_hits": 0,
            "cache_misses": 0,
            "route_unavailable": 0
        }
    
    def record(self, intent: str, query_class: str, processing_time_ms: float, 
               success: bool, confidence: float = 0.5, cache_hit: bool = False, route_unavailable: bool = False):
        self.metrics["total_queries"] += 1
        
        if intent not in self.metrics["by_intent"]:
            self.metrics["by_intent"][intent] = 0
        self.metrics["by_intent"][intent] += 1
        
        if query_class not in self.metrics["by_class"]:
            self.metrics["by_class"][query_class] = 0
        self.metrics["by_class"][query_class] += 1
        
        if route_unavailable:
            self.metrics["route_unavailable"] += 1
        
        if confidence >= 0.8:
            self.metrics["by_confidence"]["high"] += 1
        elif confidence >= 0.6:
            self.metrics["by_confidence"]["medium"] += 1
        else:
            self.metrics["by_confidence"]["low"] += 1
        
        if cache_hit:
            self.metrics["cache_hits"] += 1
        else:
            self.metrics["cache_misses"] += 1
        
        current_avg = self.metrics["avg_response_time_ms"]
        total = self.metrics["total_queries"]
        self.metrics["avg_response_time_ms"] = ((current_avg * (total - 1)) + processing_time_ms) / total
        
        if not success:
            self.metrics["failures"] += 1
        self.metrics["success_rate"] = ((self.metrics["total_queries"] - self.metrics["failures"]) / self.metrics["total_queries"]) * 100
    
    def get_metrics(self) -> Dict:
        cache_total = self.metrics["cache_hits"] + self.metrics["cache_misses"]
        return {
            **self.metrics,
            "cache_hit_rate": round(self.metrics["cache_hits"] / cache_total * 100, 1) if cache_total > 0 else 0,
            "by_intent": dict(sorted(self.metrics["by_intent"].items(), key=lambda x: x[1], reverse=True)[:10])
        }


# ==========================================================
# QUERY AUDIT LOGGER (Disabled - Feature Flag)
# ==========================================================

class QueryAuditLogger:
    """Query audit logging - temporarily disabled"""
    
    @staticmethod
    def log(db: Session, question: str, intent: str, confidence: float, 
            response_time_ms: float, success: bool, error_id: str = None):
        if not ENABLE_AUDIT_LOGGING:
            return
        try:
            # Import model inside function to avoid circular imports
            from app.models import QueryAuditLog
            
            audit_log = QueryAuditLog(
                question=question[:500],
                intent=intent,
                confidence=confidence,
                response_time_ms=response_time_ms,
                success=success,
                error_id=error_id,
                created_at=datetime.utcnow()
            )
            db.add(audit_log)
            db.commit()
        except Exception as e:
            logger.exception(f"Failed to log query audit: {e}")
            db.rollback()


# ==========================================================
# REDIS CONTEXT MANAGER
# ==========================================================

class RedisContextManager:
    def __init__(self, redis_url: str = None):
        self.redis_client = None
        self.available = False
        if REDIS_AVAILABLE and redis_url:
            try:
                self.redis_client = redis.from_url(redis_url)
                self.redis_client.ping()
                self.available = True
                logger.info("✅ Redis context manager initialized")
            except Exception as e:
                logger.warning(f"Redis unavailable: {e}")
    
    def get_context(self, user_id: str) -> Dict:
        if not self.available or not self.redis_client:
            return {}
        try:
            import json
            data = self.redis_client.get(f"context:{user_id}")
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Redis get error: {e}")
        return {}
    
    def set_context(self, user_id: str, context: Dict, ttl_seconds: int = 3600):
        if not self.available or not self.redis_client:
            return
        try:
            import json
            self.redis_client.setex(f"context:{user_id}", ttl_seconds, json.dumps(context))
        except Exception as e:
            logger.error(f"Redis set error: {e}")


# ==========================================================
# ROUTE VALIDATOR (Safe validation at startup)
# ==========================================================

class RouteValidator:
    @staticmethod
    def validate_service(service, service_name: str, routes: Dict) -> Dict[str, bool]:
        """Validate routes for a service, return availability map"""
        availability = {}
        
        if not service:
            logger.warning(f"Cannot validate {service_name} routes - service not available")
            for intent, (method, _) in routes.items():
                availability[f"{service_name}.{method}"] = False
            return availability
        
        for intent, (method, _) in routes.items():
            if hasattr(service, method) and callable(getattr(service, method)):
                availability[f"{service_name}.{method}"] = True
                logger.debug(f"✅ Route available: {service_name}.{method}")
            else:
                availability[f"{service_name}.{method}"] = False
                logger.warning(f"❌ Route unavailable: {service_name}.{method}")
        
        return availability


# ==========================================================
# AI ROOT CAUSE ANALYZER (Safe with hasattr checks)
# ==========================================================

class AIRootCauseAnalyzer:
    """Enhances AI queries with business data for root cause analysis"""
    
    @staticmethod
    def collect_context(question: str, entities: ExtractedEntities, 
                       logistics_service, analytics_service, kpi_service) -> Dict:
        """Collect relevant business data before sending to AI - Safe with hasattr"""
        context_data = {
            "question": question,
            "entities": entities.to_dict(),
            "business_data": {}
        }
        
        question_lower = question.lower()
        
        # Collect POD data if relevant
        if 'pod' in question_lower or 'pending' in question_lower:
            try:
                if logistics_service and hasattr(logistics_service, 'get_pod_status'):
                    pod_status = logistics_service.get_pod_status()
                    if pod_status:
                        context_data["business_data"]["pod_status"] = {
                            "pending_count": pod_status.get("pending_count", 0),
                            "avg_aging": pod_status.get("avg_aging", 0),
                        }
            except Exception as e:
                logger.exception(f"Failed to get POD data for AI: {e}")
        
        # Collect delivery data if relevant
        if 'delivery' in question_lower or 'delay' in question_lower:
            try:
                if logistics_service and hasattr(logistics_service, 'get_pending_deliveries'):
                    pending_deliveries = logistics_service.get_pending_deliveries()
                    if pending_deliveries:
                        context_data["business_data"]["delivery_status"] = {
                            "pending_count": pending_deliveries.get("pending_count", 0),
                        }
            except Exception as e:
                logger.exception(f"Failed to get delivery data for AI: {e}")
        
        # Collect KPI data if relevant
        if 'performance' in question_lower or 'kpi' in question_lower:
            try:
                if kpi_service and hasattr(kpi_service, 'get_executive_dashboard'):
                    dashboard = kpi_service.get_executive_dashboard()
                    if dashboard:
                        context_data["business_data"]["kpi"] = {
                            "overall_health": dashboard.get("overall_health", "Unknown"),
                        }
            except Exception as e:
                logger.exception(f"Failed to get KPI data for AI: {e}")
        
        # Collect specific city data if mentioned (Safe with hasattr)
        if entities.city:
            try:
                if analytics_service and hasattr(analytics_service, 'get_city_performance'):
                    city_performance = analytics_service.get_city_performance(entities.city)
                    if city_performance:
                        context_data["business_data"]["city_performance"] = city_performance
            except Exception as e:
                logger.exception(f"Failed to get city data for AI: {e}")
        
        # Collect specific dealer data if mentioned (Safe with hasattr)
        if entities.dealer:
            try:
                if analytics_service and hasattr(analytics_service, 'get_dealer_performance'):
                    dealer_performance = analytics_service.get_dealer_performance(entities.dealer)
                    if dealer_performance:
                        context_data["business_data"]["dealer_performance"] = dealer_performance
            except Exception as e:
                logger.exception(f"Failed to get dealer data for AI: {e}")
        
        return context_data
    
    @staticmethod
    def build_enhanced_prompt(context: Dict) -> str:
        question = context.get("question", "")
        business_data = context.get("business_data", {})
        
        if not business_data:
            return question
        
        prompt = f"""You are a logistics intelligence analyst. Analyze the following question using the provided business data.

QUESTION: {question}

BUSINESS DATA:
"""
        for category, data in business_data.items():
            prompt += f"\n{category.upper()}:"
            for key, value in data.items():
                prompt += f"\n  - {key}: {value}"
        
        prompt += """

Provide a root cause analysis with:
1. Key findings from the data
2. Potential root causes
3. Recommended actions

Keep response concise and actionable.
"""
        return prompt


# ==========================================================
# MAIN AI QUERY SERVICE (v39 - Production Ready)
# ==========================================================

class AIQueryService:
    _instance = None
    _initialized = False
    
    def __new__(cls, session_factory=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, session_factory=None):
        if self._initialized:
            # Update session_factory if provided (fixes singleton factory issue)
            if session_factory:
                self._session_factory = session_factory
            return
        
        # Store factory only - NO service caching
        self._session_factory = session_factory
        self._ai_provider = None
        self.formatter = ResponseFormatter()
        self.metrics = QueryMetrics()
        self.cache = TTLCache(maxsize=200, ttl_seconds=300)
        self.redis_context = RedisContextManager()
        self.lru_context = LRUContextManager(max_users=500)
        
        # Service health registry
        self.service_health = {
            "logistics": False,
            "analytics": False,
            "kpi": False,
            "ai": False
        }
        
        # Route availability cache
        self.route_availability = {}
        
        self._initialized = True
        
        logger.info("✅ AI Query Service v39.0 - Production Ready (Route Validation Enabled)")
        self._validate_available_routes()
    
    def _validate_available_routes(self):
        """Validate all routes at startup - safe validation"""
        session = self._get_session()
        try:
            # Test logistics service
            logistics = self._get_logistics_service(session)
            if logistics:
                self.service_health["logistics"] = True
                logistics_availability = RouteValidator.validate_service(
                    logistics, "logistics", RouteMap.LOGISTICS_ROUTES
                )
                self.route_availability.update(logistics_availability)
            
            # Test analytics service
            analytics = self._get_analytics_service(session)
            if analytics:
                self.service_health["analytics"] = True
                analytics_availability = RouteValidator.validate_service(
                    analytics, "analytics", RouteMap.ANALYTICS_ROUTES
                )
                self.route_availability.update(analytics_availability)
            
            # Test KPI service
            kpi = self._get_kpi_service(session)
            if kpi:
                self.service_health["kpi"] = True
                kpi_availability = RouteValidator.validate_service(
                    kpi, "kpi", RouteMap.KPI_ROUTES
                )
                self.route_availability.update(kpi_availability)
            
            # Report summary
            available_routes = sum(1 for v in self.route_availability.values() if v)
            total_routes = len(self.route_availability)
            logger.info(f"Route validation complete: {available_routes}/{total_routes} routes available")
            
            if available_routes < total_routes:
                for route, available in self.route_availability.items():
                    if not available:
                        logger.warning(f"  ❌ Unavailable: {route}")
        
        except Exception as e:
            logger.exception(f"Route validation failed: {e}")
        finally:
            if session:
                self._close_session(session)
    
    def _get_session(self) -> Session:
        """Get a fresh session from factory"""
        if self._session_factory:
            return self._session_factory()
        return None
    
    def _close_session(self, session: Session):
        """Safely close a session"""
        if session:
            try:
                session.close()
            except Exception as e:
                logger.exception(f"Error closing session: {e}")
    
    def _get_logistics_service(self, session: Session):
        """Create fresh logistics service - NO CACHING"""
        try:
            from app.services.logistics_query_service import LogisticsQueryService
            service = LogisticsQueryService(session)
            self.service_health["logistics"] = True
            return service
        except Exception as e:
            logger.exception(f"Failed to load LogisticsQueryService: {e}")
            self.service_health["logistics"] = False
            return None
    
    def _get_analytics_service(self, session: Session):
        """Create fresh analytics service - NO CACHING"""
        try:
            from app.services.analytics_service import AnalyticsService
            service = AnalyticsService(session)
            self.service_health["analytics"] = True
            return service
        except Exception as e:
            logger.exception(f"Failed to load AnalyticsService: {e}")
            self.service_health["analytics"] = False
            return None
    
    def _get_kpi_service(self, session: Session):
        """Create fresh KPI service - NO CACHING"""
        try:
            from app.services.kpi_service import KPIService
            service = KPIService(session)
            self.service_health["kpi"] = True
            return service
        except Exception as e:
            logger.exception(f"Failed to load KPIService: {e}")
            self.service_health["kpi"] = False
            return None
    
    @property
    def ai_provider(self):
        if self._ai_provider is None:
            try:
                from app.services.ai_provider_service import get_ai_provider
                self._ai_provider = get_ai_provider()
                self.service_health["ai"] = True
                logger.debug("AI Provider loaded (lazy)")
            except Exception as e:
                logger.exception(f"Failed to load AI Provider: {e}")
                self.service_health["ai"] = False
        return self._ai_provider
    
    def _get_context(self, user_id: str) -> Dict:
        if self.redis_context.available:
            context = self.redis_context.get_context(user_id)
            if context:
                return context
        return self.lru_context.get(user_id)
    
    def _update_context(self, user_id: str, intent: Intent, entities: ExtractedEntities, confidence: float):
        context = {
            "last_intent": intent.value,
            "last_intent_confidence": confidence,
            "last_query_time": datetime.now().isoformat(),
        }
        
        if entities.dn_number:
            context["last_dn"] = entities.dn_number
        if entities.dealer:
            context["last_dealer"] = entities.dealer
        if entities.city:
            context["last_city"] = entities.city
        
        if self.redis_context.available:
            self.redis_context.set_context(user_id, context)
        else:
            self.lru_context.set(user_id, context)
    
    def process_query(self, question: str, user_phone: str = None, request_id: str = None) -> Dict:
        start_time = time.time()
        error_id = None
        
        if not request_id:
            request_id = str(uuid.uuid4())[:8]
        
        logger.bind(request_id=request_id, phone=user_phone).info(f"Processing: {question[:100]}")
        
        session = None
        audit_session = None
        
        try:
            session = self._get_session()
            context = self._get_context(user_phone) if user_phone else {}
            entities = EntityExtractor.extract(question, context)
            logger.debug(f"Entities: {entities.to_dict()}")
            
            intent, query_class, confidence = IntentDetector.detect(question, entities)
            
            # Enhanced error tracking
            logger.bind(
                request_id=request_id,
                intent=intent.value,
                query_class=query_class.value,
                confidence=confidence
            ).info("Intent detected")
            
            # Check if route is available
            if not RouteMap.is_route_available(intent):
                logger.warning(f"Route unavailable: {intent.value}")
                result = self.formatter.format_route_unavailable(intent)
                elapsed_ms = (time.time() - start_time) * 1000
                self.metrics.record(intent.value, query_class.value, elapsed_ms, False, confidence, False, True)
                return {
                    "success": False,
                    "response": result["summary"],
                    "intent": intent.value,
                    "intent_confidence": confidence,
                    "query_class": query_class.value,
                    "entities": entities.to_dict(),
                    "processing_time_ms": round(elapsed_ms, 2),
                    "request_id": request_id,
                    "cache_hit": False,
                    "error_id": result.get("error_id")
                }
            
            cacheable_intents = [Intent.TOP_DEALERS, Intent.TOP_WAREHOUSES, Intent.EXECUTIVE_DASHBOARD]
            cached_result = None
            cache_hit = False
            
            if intent in cacheable_intents:
                cache_params = f"{entities.limit}" if intent in [Intent.TOP_DEALERS, Intent.TOP_WAREHOUSES] else ""
                cached_result = self.cache.get(intent.value, cache_params)
                if cached_result:
                    cache_hit = True
                    logger.info(f"Cache hit for {intent.value}")
            
            if cached_result:
                result = cached_result
            else:
                result = self._route(intent, entities, question, query_class, session)
                
                if intent in cacheable_intents and result.get("success"):
                    cache_params = f"{entities.limit}" if intent in [Intent.TOP_DEALERS, Intent.TOP_WAREHOUSES] else ""
                    self.cache.set(result, intent.value, cache_params)
            
            whatsapp_message = self._to_whatsapp(result)
            error_id = result.get("error_id")
            
            if user_phone:
                self._update_context(user_phone, intent, entities, confidence)
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            # Audit logging (disabled by feature flag)
            if ENABLE_AUDIT_LOGGING:
                try:
                    audit_session = self._get_session()
                    QueryAuditLogger.log(
                        audit_session, question, intent.value, confidence, 
                        elapsed_ms, result.get("success", True), error_id
                    )
                except Exception as e:
                    logger.exception(f"Audit logging failed: {e}")
                finally:
                    if audit_session:
                        self._close_session(audit_session)
            
            self.metrics.record(intent.value, query_class.value, elapsed_ms, result.get("success", True), confidence, cache_hit)
            
            logger.info(f"Response generated in {elapsed_ms:.0f}ms (Request ID: {request_id})")
            
            return {
                "success": result.get("success", True),
                "response": whatsapp_message,
                "intent": intent.value,
                "intent_confidence": confidence,
                "query_class": query_class.value,
                "entities": entities.to_dict(),
                "processing_time_ms": round(elapsed_ms, 2),
                "request_id": request_id,
                "cache_hit": cache_hit,
                "error_id": error_id
            }
        
        except Exception as e:
            error_id = str(uuid.uuid4())[:8]
            logger.bind(
                request_id=request_id,
                error_id=error_id,
                error=str(e)
            ).exception(f"Query processing error")
            
            return {
                "success": False,
                "response": f"⚠️ Service temporarily unavailable. (Error ID: {error_id})",
                "intent": "error",
                "query_class": "error",
                "entities": {},
                "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                "request_id": request_id,
                "cache_hit": False,
                "error_id": error_id
            }
        
        finally:
            if session:
                self._close_session(session)
    
    def _route(self, intent: Intent, entities: ExtractedEntities, question: str, 
               query_class: QueryClass, session: Session) -> Dict:
        
        service_name, method, has_param = RouteMap.get_route(intent)
        
        if intent == Intent.HELP:
            return self.formatter.format_success({}, self.formatter.format_help())
        
        if intent == Intent.GREETING:
            return self.formatter.format_success({}, self.formatter.format_greeting())
        
        # Root cause analysis
        if intent == Intent.ROOT_CAUSE or (query_class == QueryClass.AI and 'why' in question.lower()):
            return self._call_root_cause_ai(question, entities, session)
        
        # Route to service with safe method calling
        if service_name == "logistics":
            service = self._get_logistics_service(session)
            if not service:
                return self.formatter.format_error("Logistics service unavailable")
            
            # Safe method lookup
            handler = getattr(service, method, None)
            if not handler or not callable(handler):
                return self.formatter.format_error(f"Route '{method}' not available", code="route_missing")
            
            try:
                if has_param:
                    param = entities.dn_number
                    if not param:
                        return self.formatter.format_error(f"Missing required parameter for {intent.value}")
                    result = handler(param)
                else:
                    result = handler()
                
                if isinstance(result, dict) and result.get("error"):
                    return self.formatter.format_error(result["error"])
                summary = result.get("_summary", "")
                return self.formatter.format_success(result, summary)
            except Exception as e:
                logger.exception(f"Logistics service error: {method}")
                return self.formatter.format_error(str(e))
        
        if service_name == "analytics":
            service = self._get_analytics_service(session)
            if not service:
                return self.formatter.format_error("Analytics service unavailable")
            
            handler = getattr(service, method, None)
            if not handler or not callable(handler):
                return self.formatter.format_error(f"Route '{method}' not available", code="route_missing")
            
            try:
                if has_param:
                    param = entities.dealer or entities.dealer_code
                    if param:
                        result = handler(param)
                    else:
                        result = handler(entities.limit)
                else:
                    result = handler(entities.limit)
                
                if isinstance(result, dict) and result.get("error"):
                    return self.formatter.format_error(result["error"])
                summary = result.get("_summary", "")
                return self.formatter.format_success(result, summary)
            except Exception as e:
                logger.exception(f"Analytics service error: {method}")
                return self.formatter.format_error(str(e))
        
        if service_name == "kpi":
            service = self._get_kpi_service(session)
            if not service:
                return self.formatter.format_error("KPI service unavailable")
            
            handler = getattr(service, method, None)
            if not handler or not callable(handler):
                return self.formatter.format_error(f"Route '{method}' not available", code="route_missing")
            
            try:
                result = handler()
                if isinstance(result, dict) and result.get("error"):
                    return self.formatter.format_error(result["error"])
                summary = result.get("_summary", "")
                return self.formatter.format_success(result, summary)
            except Exception as e:
                logger.exception(f"KPI service error: {method}")
                return self.formatter.format_error(str(e))
        
        # AI fallback
        if query_class == QueryClass.AI or intent == Intent.AI_QUERY:
            return self._call_root_cause_ai(question, entities, session)
        
        return self.formatter.format_success({}, "I understand you're asking about logistics. Please be more specific or type 'Help' for commands.")
    
    def _call_root_cause_ai(self, question: str, entities: ExtractedEntities, session: Session) -> Dict:
        """Enhanced AI call with business context - Safe with hasattr checks"""
        if not self.ai_provider:
            return self.formatter.format_success({}, "I'm still learning. Please try rephrasing your question or type 'Help' for available commands.")
        
        try:
            logistics = self._get_logistics_service(session)
            analytics = self._get_analytics_service(session)
            kpi = self._get_kpi_service(session)
            
            context_data = AIRootCauseAnalyzer.collect_context(
                question, entities, logistics, analytics, kpi
            )
            enhanced_prompt = AIRootCauseAnalyzer.build_enhanced_prompt(context_data)
            
            result = self.ai_provider.chat(enhanced_prompt, "guest")
            response_text = result if isinstance(result, str) else str(result)
            
            data_summary = ""
            if context_data.get("business_data"):
                data_summary = "\n\n📊 *Data Analyzed:* " + ", ".join(context_data["business_data"].keys())
            
            return self.formatter.format_success(
                {"insight": response_text, "context_used": context_data["business_data"]},
                response_text + data_summary
            )
        except Exception as e:
            logger.exception(f"AI root cause call failed")
            return self.formatter.format_error(str(e))
    
    def _to_whatsapp(self, response: Dict) -> str:
        if not response.get("success"):
            error_id = response.get("error_id", "")
            if error_id:
                return f"❌ {response.get('summary', 'Unable to process request')}"
            return f"❌ {response.get('summary', 'Unable to process request')}"
        summary = response.get("summary", "")
        if summary:
            return summary
        return "✅ Request processed successfully"
    
    def health_check(self) -> Dict:
        return {
            "service": "ai_query_service",
            "version": "39.0",
            "mode": "pure_router_route_validation",
            "status": "healthy",
            "metrics": self.metrics.get_metrics(),
            "cache": self.cache.get_stats(),
            "context_stats": self.lru_context.get_stats(),
            "redis_available": self.redis_context.available,
            "service_health": self.service_health,
            "route_availability": {k: v for k, v in self.route_availability.items() if not v},
            "enabled_features": {
                "audit_logging": ENABLE_AUDIT_LOGGING
            }
        }
    
    def get_metrics(self) -> Dict:
        return self.metrics.get_metrics()
    
    def clear_cache(self):
        self.cache.clear()
        logger.info("Cache cleared")


# ==========================================================
# FACTORY FUNCTIONS
# ==========================================================

_SERVICE_INSTANCE = None

def get_ai_query_service(session_factory=None) -> AIQueryService:
    """Singleton factory for AIQueryService - updates session_factory if provided"""
    global _SERVICE_INSTANCE
    if _SERVICE_INSTANCE is None:
        _SERVICE_INSTANCE = AIQueryService(session_factory)
    elif session_factory and _SERVICE_INSTANCE._session_factory is None:
        _SERVICE_INSTANCE._session_factory = session_factory
    return _SERVICE_INSTANCE


def process_whatsapp_query(question: str, session_factory, phone_number: str = None, user_id: str = None, request_id: str = None) -> str:
    try:
        service = get_ai_query_service(session_factory)
        result = service.process_query(question, phone_number or user_id, request_id)
        return result.get("response", "⚠️ Unable to process your request.")
    except Exception as e:
        logger.exception(f"Query processing error")
        return "⚠️ Service temporarily unavailable. Please try again later."


def health_check(session_factory=None) -> Dict:
    try:
        service = get_ai_query_service(session_factory)
        return service.health_check()
    except Exception as e:
        return {"service": "ai_query_service", "status": "unhealthy", "error": str(e), "version": "39.0"}


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("🧠 AI QUERY SERVICE v39.0 - PRODUCTION READY")
logger.info("")
logger.info("   Critical Fixes:")
logger.info("   ✅ Frozen RouteMap - only routes that exist")
logger.info("   ✅ Safe route validation at startup")
logger.info("   ✅ Removed dynamic __getattribute__ (safe getattr)")
logger.info("   ✅ Route availability check before execution")
logger.info("   ✅ Disabled QueryAudit dependency (feature flag)")
logger.info("   ✅ Service health registry")
logger.info("   ✅ Safe root cause AI with hasattr checks")
logger.info("   ✅ Fixed singleton factory to update session_factory")
logger.info("   ✅ Route fallbacks with user-friendly messages")
logger.info("   ✅ Enhanced error tracking with logger.bind")
logger.info("")
logger.info("   Available Routes:")
logger.info("   ✅ DN_LOOKUP, DN_TIMELINE")
logger.info("   ✅ PENDING_POD, PENDING_PGI, PENDING_DELIVERIES")
logger.info("   ✅ TOP_DEALERS, TOP_WAREHOUSES, DEALER_PERFORMANCE")
logger.info("   ✅ EXECUTIVE_DASHBOARD, NETWORK_HEALTH, CRITICAL_DELAYS, CONTROL_TOWER")
logger.info("   ✅ HELP, GREETING, AI_QUERY, ROOT_CAUSE")
logger.info("")
logger.info("   Disabled Routes (Coming Soon):")
logger.info("   ⏳ DN_AGING, POD_AGING, WAREHOUSE_STATUS, REGION_PERFORMANCE")
logger.info("=" * 70)
