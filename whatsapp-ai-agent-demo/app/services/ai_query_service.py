# ==========================================================
# FILE: app/services/ai_query_service.py (IMPROVED v42.0 - AI-FIRST ARCHITECTURE)
# ==========================================================
# PURPOSE: INTELLIGENT ORCHESTRATOR - Never Fail, Always Answer
#
# CORE PRINCIPLES v42.0:
# - AI-FIRST FALLBACK: Always try AI before showing errors
# - NO "FEATURE UNDER DEVELOPMENT": Never show this to users
# - UNIVERSAL AI MODE: All intents can use AI understanding
# - SERVICE ISOLATION: Service failures never break the bot
# - CONVERSATION MEMORY: Context-aware responses
# - SELF-HEALING: Graceful degradation
# ==========================================================

from __future__ import annotations

import re
import time
import hashlib
import uuid
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
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
ENABLE_AUDIT_LOGGING = False


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
# LRU CONTEXT MANAGER (Enhanced with Conversation Memory)
# ==========================================================

@dataclass
class ConversationMemory:
    """Stores conversation context for a user"""
    last_question: str = ""
    last_intent: str = ""
    last_response: str = ""
    last_dn: Optional[str] = None
    last_city: Optional[str] = None
    last_dealer: Optional[str] = None
    last_warehouse: Optional[str] = None
    conversation_history: List[Dict] = field(default_factory=list)
    last_interaction_time: datetime = field(default_factory=datetime.now)
    interaction_count: int = 0
    
    def add_exchange(self, question: str, response: str, intent: str):
        """Add a question-answer exchange to history"""
        self.conversation_history.append({
            "question": question,
            "response": response[:200],  # Truncate for memory efficiency
            "intent": intent,
            "timestamp": datetime.now().isoformat()
        })
        # Keep only last 10 exchanges
        if len(self.conversation_history) > 10:
            self.conversation_history = self.conversation_history[-10:]
        self.last_question = question
        self.last_response = response
        self.last_intent = intent
        self.last_interaction_time = datetime.now()
        self.interaction_count += 1
    
    def get_context_summary(self) -> str:
        """Get a summary of conversation context for AI"""
        if not self.conversation_history:
            return ""
        
        summary = f"Previous interactions ({len(self.conversation_history)} exchanges):\n"
        for i, exchange in enumerate(self.conversation_history[-3:], 1):  # Last 3 exchanges
            summary += f"{i}. User: {exchange['question'][:100]}\n"
        return summary


class LRUContextManager:
    """LRU-based context manager with conversation memory"""
    
    def __init__(self, max_users: int = 500):
        self.max_users = max_users
        self.contexts = OrderedDict()
        self.conversation_memory: Dict[str, ConversationMemory] = {}
    
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
                if oldest_key in self.conversation_memory:
                    del self.conversation_memory[oldest_key]
                logger.debug(f"Evicted context for user {oldest_key}")
        self.contexts[user_id] = context
    
    def get_memory(self, user_id: str) -> ConversationMemory:
        """Get or create conversation memory for user"""
        if user_id not in self.conversation_memory:
            self.conversation_memory[user_id] = ConversationMemory()
        return self.conversation_memory[user_id]
    
    def update_memory(self, user_id: str, question: str, response: str, intent: str, entities: 'ExtractedEntities'):
        """Update conversation memory with new exchange"""
        memory = self.get_memory(user_id)
        memory.add_exchange(question, response, intent)
        
        # Update entity memory
        if entities.dn_number:
            memory.last_dn = entities.dn_number
        if entities.city:
            memory.last_city = entities.city
        if entities.dealer:
            memory.last_dealer = entities.dealer
        if entities.warehouse:
            memory.last_warehouse = entities.warehouse
    
    def get_size(self) -> int:
        return len(self.contexts)
    
    def get_stats(self) -> Dict:
        return {
            "size": len(self.contexts),
            "max_users": self.max_users,
            "active_memories": len(self.conversation_memory),
            "utilization": round(len(self.contexts) / self.max_users * 100, 1)
        }


# ==========================================================
# INTENT TYPES
# ==========================================================

class Intent(str, Enum):
    # DN Operations
    DN_LOOKUP = "dn_lookup"
    DN_TIMELINE = "dn_timeline"
    
    # POD Operations
    PENDING_POD = "pending_pod"
    
    # PGI Operations
    PENDING_PGI = "pending_pgi"
    
    # Delivery Operations
    PENDING_DELIVERIES = "pending_deliveries"
    
    # Dealer Operations
    DEALER_PERFORMANCE = "dealer_performance"
    TOP_DEALERS = "top_dealers"
    
    # Warehouse Operations
    TOP_WAREHOUSES = "top_warehouses"
    
    # KPI Operations
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    NETWORK_HEALTH = "network_health"
    CRITICAL_DELAYS = "critical_delays"
    CONTROL_TOWER = "control_tower"
    
    # AI/General (All routed to AI)
    HELP = "help"
    GREETING = "greeting"
    GENERAL = "general"
    AI_QUERY = "ai_query"
    ROOT_CAUSE = "root_cause"
    UNKNOWN = "unknown"


class QueryClass(str, Enum):
    OPERATIONAL = "operational"
    ANALYTICAL = "analytical"
    EXECUTIVE = "executive"
    AI = "ai"


# ==========================================================
# ENTITY EXTRACTION (Enhanced)
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
    
    CITIES = [
        'karachi', 'lahore', 'islamabad', 'rawalpindi', 'faisalabad', 
        'multan', 'peshawar', 'quetta', 'gujranwala', 'sialkot',
        'hyderabad', 'sukkur', 'bahawalpur', 'sahiwal', 'jhelum', 'sargodha'
    ]
    
    @classmethod
    def extract(cls, question: str, context: Dict = None, memory=None) -> ExtractedEntities:
        question_lower = question.lower().strip()
        entities = ExtractedEntities()
        
        if context:
            entities.last_intent = context.get("last_intent")
            entities.last_dn = context.get("last_dn")
            entities.last_dealer = context.get("last_dealer")
            entities.last_city = context.get("last_city")
        
        # Use memory for fallback entities
        if memory:
            if not entities.last_dn and memory.last_dn:
                entities.last_dn = memory.last_dn
            if not entities.last_dealer and memory.last_dealer:
                entities.last_dealer = memory.last_dealer
            if not entities.last_city and memory.last_city:
                entities.last_city = memory.last_city
        
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
# INTENT DETECTION (With Confidence Scoring)
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
        
        return QueryClass.OPERATIONAL
    
    @classmethod
    def detect(cls, question: str, entities: ExtractedEntities) -> Tuple[Intent, QueryClass, float]:
        question_lower = question.lower().strip()
        
        # Direct matches (100% confidence)
        if question_lower in ['help', 'menu', 'commands']:
            return Intent.HELP, QueryClass.OPERATIONAL, 1.0
        
        if question_lower in ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']:
            return Intent.GREETING, QueryClass.OPERATIONAL, 1.0
        
        # Root cause / analysis questions (high confidence for AI)
        analysis_keywords = ['why', 'root cause', 'reason', 'what caused', 'how to fix', 
                            'what should we do', 'can you help', 'what issue', 'any risk',
                            'help me', 'suggest', 'recommend', 'analysis', 'insight']
        if any(kw in question_lower for kw in analysis_keywords):
            return Intent.ROOT_CAUSE, QueryClass.AI, 0.85
        
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
        
        # Calculate confidence score for keyword matches
        best_intent = None
        best_score = 0.0
        
        for intent, keywords in cls.KEYWORD_GROUPS.items():
            score = 0.0
            for keyword in keywords:
                if keyword in question_lower:
                    score += 1.0
            if score > best_score:
                best_score = score
                best_intent = intent
        
        # If high confidence, return matched intent
        if best_intent and best_score >= 1.0:
            confidence = min(0.70 + (best_score / 20), 0.95)
            query_class = cls.classify_query(question)
            return best_intent, query_class, confidence
        
        # Low confidence - route to AI for understanding
        confidence = max(0.4, best_score / 10) if best_score > 0 else 0.4
        return Intent.AI_QUERY, QueryClass.AI, confidence


# ==========================================================
# BUSINESS CONTEXT BUILDER
# ==========================================================

class BusinessContextBuilder:
    """Builds rich context for AI queries from available data"""
    
    @staticmethod
    def build_context(
        question: str, 
        entities: ExtractedEntities, 
        memory=None,
        logistics_data: Dict = None,
        analytics_data: Dict = None,
        kpi_data: Dict = None
    ) -> Dict:
        """Build comprehensive context for AI"""
        
        context = {
            "question": question,
            "entities": entities.to_dict(),
            "business_data": {},
            "user_context": {}
        }
        
        # Add conversation memory
        if memory:
            context["user_context"]["conversation_summary"] = memory.get_context_summary()
            context["user_context"]["interaction_count"] = memory.interaction_count
            if memory.last_city:
                context["user_context"]["last_mentioned_city"] = memory.last_city
            if memory.last_dealer:
                context["user_context"]["last_mentioned_dealer"] = memory.last_dealer
            if memory.last_dn:
                context["user_context"]["last_tracked_dn"] = memory.last_dn
        
        # Add any available business data (gracefully handle missing)
        if logistics_data:
            context["business_data"]["logistics"] = logistics_data
        if analytics_data:
            context["business_data"]["analytics"] = analytics_data
        if kpi_data:
            context["business_data"]["kpi"] = kpi_data
        
        return context
    
    @staticmethod
    def build_enhanced_prompt(context: Dict) -> str:
        """Build enhanced prompt for AI with context"""
        question = context.get("question", "")
        business_data = context.get("business_data", {})
        user_context = context.get("user_context", {})
        
        prompt = f"""You are a logistics intelligence analyst for a supply chain operations team. 
Answer the user's question based on available data. Be concise, actionable, and professional.

USER QUESTION: {question}

"""
        if user_context.get("conversation_summary"):
            prompt += f"\nCONVERSATION CONTEXT:\n{user_context['conversation_summary']}\n"
        
        if business_data:
            prompt += "\nAVAILABLE DATA:\n"
            for category, data in business_data.items():
                if data:
                    prompt += f"\n{category.upper()}:\n"
                    if isinstance(data, dict):
                        for key, value in list(data.items())[:5]:  # Limit to 5 items
                            prompt += f"  - {key}: {value}\n"
                    elif isinstance(data, list):
                        for item in data[:3]:  # Limit to 3 items
                            prompt += f"  - {item}\n"
        else:
            prompt += "\nNote: Specific business data is currently limited. Provide general logistics guidance based on best practices.\n"
        
        prompt += """
RESPONSE GUIDELINES:
1. If specific data is available, use it directly
2. If data is limited, provide general logistics best practices
3. Always be helpful - never say "I don't know" without offering alternatives
4. Suggest specific commands the user can try (e.g., "Pending POD", "Top dealers")
5. Keep responses concise and actionable

Your response:"""
        
        return prompt


# ==========================================================
# RESPONSE FORMATTER (WhatsApp Optimized)
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
    def format_ai_response(response_text: str, context_used: Dict = None) -> str:
        """Format AI response for WhatsApp with proper structure"""
        if not response_text:
            return "✅ Request processed successfully."
        
        # If response already has markdown formatting, return as-is
        if "📊" in response_text or "*" in response_text:
            return response_text
        
        # Format structured response
        lines = response_text.strip().split('\n')
        formatted_lines = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Add emojis for different sections
            if line.lower().startswith('issue'):
                formatted_lines.append(f"📋 *{line}*")
            elif line.lower().startswith('root cause'):
                formatted_lines.append(f"🔍 *{line}*")
            elif line.lower().startswith('recommend'):
                formatted_lines.append(f"💡 *{line}*")
            elif line.lower().startswith('action'):
                formatted_lines.append(f"✅ *{line}*")
            elif line.startswith('-') or line.startswith('•'):
                formatted_lines.append(f"  {line}")
            else:
                formatted_lines.append(line)
        
        return '\n'.join(formatted_lines)
    
    @staticmethod
    def format_help() -> str:
        return """
🤖 *AI LOGISTICS ASSISTANT - HELP* v42.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• Send DN number (starts with 80)

📋 *Pending Items*
• `Pending POD` - Missing proofs
• `Pending PGI` - Pending dispatches
• `Pending deliveries` - Undelivered

🏪 *Analytics*
• `Top dealers` - Rankings
• `Dealer ABC performance` - Specific dealer

📊 *Executive Dashboard*
• `Executive dashboard` - KPI overview
• `Network health` - System status
• `Control tower` - All alerts

💬 *AI Assistant*
• Just ask anything - I'll help!
• "Why is Lahore delayed?"
• "What issues should I know about?"
• "How can I improve delivery times?"

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

I'm your *AI Logistics Assistant v42.0*. 

I can help you:
• Track DNs with any 10+ digit number
• Check pending PODs, PGIs, and deliveries
• Show dealer and warehouse rankings
• Analyze issues and provide recommendations

Type `Help` to see all commands, or just ask me anything!
"""


# ==========================================================
# CENTRAL ROUTE MAP (With Validation)
# ==========================================================

class RouteMap:
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
    
    # Route availability cache (populated at startup)
    _available_routes: Dict[str, bool] = {}
    _missing_routes: Dict[str, str] = {}
    
    @classmethod
    def is_route_available(cls, intent: Intent) -> bool:
        """Check if route is available"""
        return intent in cls.LOGISTICS_ROUTES or \
               intent in cls.ANALYTICS_ROUTES or \
               intent in cls.KPI_ROUTES
    
    @classmethod
    def get_route(cls, intent: Intent) -> Tuple[Optional[str], Optional[str], bool]:
        """Get route for intent: (service, method, has_param)"""
        
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
    
    @classmethod
    def validate_routes(cls, logistics_service, analytics_service, kpi_service):
        """Validate all routes at startup"""
        cls._available_routes = {}
        cls._missing_routes = {}
        
        # Validate logistics routes
        if logistics_service:
            for intent, (method, _) in cls.LOGISTICS_ROUTES.items():
                if hasattr(logistics_service, method) and callable(getattr(logistics_service, method)):
                    cls._available_routes[f"logistics.{method}"] = True
                else:
                    cls._available_routes[f"logistics.{method}"] = False
                    cls._missing_routes[f"logistics.{method}"] = "Method not found"
        
        # Validate analytics routes
        if analytics_service:
            for intent, (method, _) in cls.ANALYTICS_ROUTES.items():
                if hasattr(analytics_service, method) and callable(getattr(analytics_service, method)):
                    cls._available_routes[f"analytics.{method}"] = True
                else:
                    cls._available_routes[f"analytics.{method}"] = False
                    cls._missing_routes[f"analytics.{method}"] = "Method not found"
        
        # Validate KPI routes
        if kpi_service:
            for intent, (method, _) in cls.KPI_ROUTES.items():
                if hasattr(kpi_service, method) and callable(getattr(kpi_service, method)):
                    cls._available_routes[f"kpi.{method}"] = True
                else:
                    cls._available_routes[f"kpi.{method}"] = False
                    cls._missing_routes[f"kpi.{method}"] = "Method not found"
        
        return cls._available_routes, cls._missing_routes


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
            "ai_fallbacks": 0,  # Track AI fallback usage
            "by_confidence": {"high": 0, "medium": 0, "low": 0},
            "cache_hits": 0,
            "cache_misses": 0,
            "service_failures": {
                "logistics": 0,
                "analytics": 0,
                "kpi": 0,
                "ai": 0
            }
        }
    
    def record(self, intent: str, query_class: str, processing_time_ms: float, 
               success: bool, confidence: float = 0.5, cache_hit: bool = False, 
               service_failure: str = None, ai_fallback: bool = False):
        self.metrics["total_queries"] += 1
        
        if intent not in self.metrics["by_intent"]:
            self.metrics["by_intent"][intent] = 0
        self.metrics["by_intent"][intent] += 1
        
        if query_class not in self.metrics["by_class"]:
            self.metrics["by_class"][query_class] = 0
        self.metrics["by_class"][query_class] += 1
        
        if ai_fallback:
            self.metrics["ai_fallbacks"] += 1
        
        if service_failure and service_failure in self.metrics["service_failures"]:
            self.metrics["service_failures"][service_failure] += 1
        
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
# MAIN AI QUERY SERVICE (v42.0 - AI-FIRST ARCHITECTURE)
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
            if session_factory:
                self._session_factory = session_factory
            return
        
        self._session_factory = session_factory
        self._ai_provider = None
        self.formatter = ResponseFormatter()
        self.metrics = QueryMetrics()
        self.cache = TTLCache(maxsize=200, ttl_seconds=300)
        self.redis_context = RedisContextManager()
        self.lru_context = LRUContextManager(max_users=500)
        
        self.service_health = {
            "logistics": False,
            "analytics": False,
            "kpi": False,
            "ai": False
        }
        
        self._initialized = True
        
        logger.info("=" * 70)
        logger.info("🧠 AI QUERY SERVICE v42.0 - AI-FIRST ARCHITECTURE")
        logger.info("   Principles: Never Fail | Always Answer | AI-First Fallback")
        logger.info("=" * 70)
        self._validate_available_routes()
    
    def _validate_available_routes(self):
        """Validate all routes at startup - non-blocking"""
        session = self._get_session()
        try:
            logistics = self._get_logistics_service(session)
            analytics = self._get_analytics_service(session)
            kpi = self._get_kpi_service(session)
            
            available, missing = RouteMap.validate_routes(logistics, analytics, kpi)
            
            available_count = sum(1 for v in available.values() if v)
            total_count = len(available)
            
            logger.info(f"Route validation: {available_count}/{total_count} routes available")
            if missing:
                for route, reason in list(missing.items())[:5]:
                    logger.warning(f"  ⚠️ {route}: {reason}")
                    
        except Exception as e:
            logger.exception(f"Route validation failed: {e}")
        finally:
            if session:
                self._close_session(session)
    
    def _get_session(self) -> Session:
        if self._session_factory:
            return self._session_factory()
        return None
    
    def _close_session(self, session: Session):
        if session:
            try:
                session.close()
            except Exception as e:
                logger.exception(f"Error closing session: {e}")
    
    def _get_logistics_service(self, session: Session):
        try:
            from app.services.logistics_query_service import LogisticsQueryService
            service = LogisticsQueryService(session)
            self.service_health["logistics"] = True
            return service
        except Exception as e:
            logger.debug(f"Logistics service unavailable: {e}")
            self.service_health["logistics"] = False
            return None
    
    def _get_analytics_service(self, session: Session):
        try:
            from app.services.analytics_service import AnalyticsService
            service = AnalyticsService(session)
            self.service_health["analytics"] = True
            return service
        except Exception as e:
            logger.debug(f"Analytics service unavailable: {e}")
            self.service_health["analytics"] = False
            return None
    
    def _get_kpi_service(self, session: Session):
        try:
            from app.services.kpi_service import KPIService
            service = KPIService(session)
            self.service_health["kpi"] = True
            return service
        except Exception as e:
            logger.debug(f"KPI service unavailable: {e}")
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
                logger.debug(f"AI Provider unavailable: {e}")
                self.service_health["ai"] = False
        return self._ai_provider
    
    # ==========================================================
    # UNIVERSAL AI HANDLER (Core of v42.0)
    # ==========================================================
    
    def _handle_ai_query(self, question: str, entities: ExtractedEntities, 
                         session: Session, request_id: str, context_data: Dict = None) -> Dict:
        """
        Universal AI handler for all intents.
        This is the primary fallback for ANY query that can't be handled by specific routes.
        """
        logger.bind(request_id=request_id).info(f"🤖 Universal AI handler invoked: {question[:50]}")
        self.metrics.record("ai_universal", "ai", 0, True, 0.7, False, False, True)
        
        # Try to collect business data (gracefully handle failures)
        logistics_data = None
        analytics_data = None
        kpi_data = None
        
        try:
            logistics = self._get_logistics_service(session)
            if logistics:
                # Try to get summary data (non-critical)
                try:
                    pod_status = logistics.get_pod_status() if hasattr(logistics, 'get_pod_status') else None
                    if pod_status:
                        logistics_data = {"pending_pods": pod_status.get("pending_count", "Unknown")}
                except:
                    pass
        except:
            pass
        
        try:
            analytics = self._get_analytics_service(session)
            if analytics:
                try:
                    top_dealers = analytics.get_top_dealers(3) if hasattr(analytics, 'get_top_dealers') else None
                    if top_dealers:
                        analytics_data = {"top_dealers": top_dealers[:3] if isinstance(top_dealers, list) else []}
                except:
                    pass
        except:
            pass
        
        # Build context with memory
        memory = self.lru_context.get_memory(request_id or "guest")
        context = BusinessContextBuilder.build_context(
            question=question,
            entities=entities,
            memory=memory,
            logistics_data=logistics_data,
            analytics_data=analytics_data,
            kpi_data=kpi_data
        )
        
        # If context_data provided, merge it
        if context_data:
            context["business_data"].update(context_data)
        
        # Generate AI response
        ai_response = self._generate_ai_response(context, request_id)
        
        # Format for WhatsApp
        formatted_response = ResponseFormatter.format_ai_response(ai_response, context.get("business_data"))
        
        # Update memory
        self.lru_context.update_memory(request_id or "guest", question, formatted_response, "ai_universal", entities)
        
        return self.formatter.format_success(
            {"insight": ai_response, "context_used": context.get("business_data", {})},
            formatted_response
        )
    
    def _generate_ai_response(self, context: Dict, request_id: str) -> str:
        """Generate AI response using the provider"""
        if not self.ai_provider:
            logger.bind(request_id=request_id).warning("AI provider unavailable, using fallback")
            return self._get_fallback_response(context.get("question", ""))
        
        try:
            enhanced_prompt = BusinessContextBuilder.build_enhanced_prompt(context)
            user_context = context.get("entities", {}).get("customer_code") or "guest"
            
            result = self.ai_provider.chat(enhanced_prompt, user_context, request_id=request_id)
            
            if not result or len(result.strip()) == 0:
                return self._get_fallback_response(context.get("question", ""))
            
            return result
            
        except Exception as e:
            logger.bind(request_id=request_id).exception(f"AI generation failed: {e}")
            self.metrics.record("ai_error", "ai", 0, False, 0.5, False, "ai")
            return self._get_fallback_response(context.get("question", ""))
    
    def _get_fallback_response(self, question: str) -> str:
        """Intelligent fallback when AI is unavailable"""
        question_lower = question.lower()
        
        if 'help' in question_lower or 'command' in question_lower:
            return ResponseFormatter.format_help()
        
        if any(word in question_lower for word in ['hi', 'hello', 'hey', 'good']):
            return ResponseFormatter.format_greeting()
        
        return f"""I understand you're asking about: "{question[:80]}"

Here's how I can help:

📋 *Try these commands:*
• Send any 10+ digit number to track a DN
• `Pending POD` - Missing proofs
• `Top dealers` - Dealer rankings
• `Executive dashboard` - KPI overview
• `Control tower` - All alerts

💡 *Or ask me:*
• "Why is Lahore delayed?"
• "What issues should I know about?"
• "Help" for complete list

Type `Help` anytime to see all commands!"""
    
    # ==========================================================
    # ROUTE EXECUTION WITH SMART FALLBACK CHAIN
    # ==========================================================
    
    def _execute_route_with_fallback(self, intent: Intent, entities: ExtractedEntities,
                                      question: str, session: Session, request_id: str) -> Dict:
        """
        Execute route with smart fallback chain:
        1. Try specific route
        2. If fails, try AI
        3. If AI fails, show help menu
        """
        service_name, method, has_param = RouteMap.get_route(intent)
        
        # Special intents that should always go to AI
        if intent in [Intent.AI_QUERY, Intent.ROOT_CAUSE, Intent.GENERAL]:
            logger.bind(request_id=request_id).info(f"Routing {intent.value} to AI")
            return self._handle_ai_query(question, entities, session, request_id)
        
        # Try specific route if available
        if service_name:
            route_result = self._try_specific_route(
                service_name, method, has_param, intent, 
                entities, question, session, request_id
            )
            
            # If route succeeded, return result
            if route_result and route_result.get("success"):
                return route_result
            
            # Route failed, fallback to AI
            logger.bind(request_id=request_id).info(f"Route {intent.value} failed, falling back to AI")
            ai_result = self._handle_ai_query(question, entities, session, request_id, 
                                              context_data={"failed_route": intent.value})
            
            # If AI succeeded, return AI result
            if ai_result and ai_result.get("success"):
                self.metrics.record(intent.value, "ai", 0, True, 0.6, False, False, True)
                return ai_result
        
        # Final fallback: AI handler
        return self._handle_ai_query(question, entities, session, request_id)
    
    def _try_specific_route(self, service_name: str, method: str, has_param: bool,
                            intent: Intent, entities: ExtractedEntities, question: str,
                            session: Session, request_id: str) -> Optional[Dict]:
        """Try to execute a specific route, return None if fails"""
        route_start = time.time()
        
        try:
            if service_name == "logistics":
                service = self._get_logistics_service(session)
                if not service:
                    return None
                
                handler = getattr(service, method, None)
                if not handler or not callable(handler):
                    return None
                
                if has_param:
                    param = entities.dn_number
                    if not param:
                        return None
                    result = handler(param)
                else:
                    result = handler()
                
                route_time = round((time.time() - route_start) * 1000, 2)
                logger.bind(request_id=request_id, intent=intent.value).info(f"Route executed in {route_time}ms")
                
                if isinstance(result, dict):
                    if result.get("error"):
                        return None
                    summary = result.get("_summary", "")
                    return self.formatter.format_success(result, summary)
                return None
                
            elif service_name == "analytics":
                service = self._get_analytics_service(session)
                if not service:
                    return None
                
                handler = getattr(service, method, None)
                if not handler or not callable(handler):
                    return None
                
                if has_param:
                    param = entities.dealer or entities.dealer_code
                    if param:
                        result = handler(param)
                    else:
                        result = handler(entities.limit)
                else:
                    result = handler(entities.limit)
                
                route_time = round((time.time() - route_start) * 1000, 2)
                logger.bind(request_id=request_id, intent=intent.value).info(f"Route executed in {route_time}ms")
                
                if isinstance(result, dict) and result.get("error"):
                    return None
                summary = result.get("_summary", "") if isinstance(result, dict) else ""
                return self.formatter.format_success(result, summary)
                
            elif service_name == "kpi":
                service = self._get_kpi_service(session)
                if not service:
                    return None
                
                handler = getattr(service, method, None)
                if not handler or not callable(handler):
                    return None
                
                result = handler()
                
                route_time = round((time.time() - route_start) * 1000, 2)
                logger.bind(request_id=request_id, intent=intent.value).info(f"Route executed in {route_time}ms")
                
                if isinstance(result, dict) and result.get("error"):
                    return None
                summary = result.get("_summary", "") if isinstance(result, dict) else ""
                return self.formatter.format_success(result, summary)
                
        except Exception as e:
            logger.bind(request_id=request_id).debug(f"Route {method} failed: {e}")
            self.metrics.record(intent.value, "error", 0, False, 0.5, False, service_name)
            return None
        
        return None
    
    # ==========================================================
    # MAIN PROCESSING PIPELINE
    # ==========================================================
    
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
        
        if not request_id:
            request_id = str(uuid.uuid4())[:8]
        
        logger.bind(request_id=request_id, phone=user_phone).info(f"Processing: {question[:100]}")
        
        session = None
        
        try:
            session = self._get_session()
            context = self._get_context(user_phone) if user_phone else {}
            memory = self.lru_context.get_memory(user_phone or request_id)
            
            entities = EntityExtractor.extract(question, context, memory)
            logger.bind(request_id=request_id).debug(f"Entities: {entities.to_dict()}")
            
            intent, query_class, confidence = IntentDetector.detect(question, entities)
            
            logger.bind(
                request_id=request_id,
                intent=intent.value,
                query_class=query_class.value,
                confidence=confidence
            ).info("Intent detected")
            
            # SPECIAL: Always route HELP and GREETING directly
            if intent == Intent.HELP:
                result = self.formatter.format_success({}, self.formatter.format_help())
            elif intent == Intent.GREETING:
                result = self.formatter.format_success({}, self.formatter.format_greeting())
            else:
                # Use smart fallback chain for all other intents
                result = self._execute_route_with_fallback(intent, entities, question, session, request_id)
            
            whatsapp_message = result.get("summary", "") if result.get("success") else result.get("summary", "Unable to process")
            error_id = result.get("error_id")
            
            if user_phone:
                self._update_context(user_phone, intent, entities, confidence)
                self.lru_context.update_memory(user_phone, question, whatsapp_message, intent.value, entities)
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            self.metrics.record(intent.value, query_class.value, elapsed_ms, 
                               result.get("success", True), confidence, False, 
                               None, intent in [Intent.AI_QUERY, Intent.ROOT_CAUSE, Intent.GENERAL])
            
            # Auto cache cleanup every 500 queries
            if self.metrics.metrics["total_queries"] % 500 == 0:
                logger.bind(request_id=request_id).info("Auto cache cleanup triggered")
                self.cache.clear()
            
            logger.bind(request_id=request_id).info(f"Response generated in {elapsed_ms:.0f}ms")
            
            return {
                "success": result.get("success", True),
                "response": whatsapp_message,
                "intent": intent.value,
                "intent_confidence": confidence,
                "query_class": query_class.value,
                "entities": entities.to_dict(),
                "processing_time_ms": round(elapsed_ms, 2),
                "request_id": request_id,
                "cache_hit": False,
                "error_id": error_id
            }
        
        except Exception as e:
            error_id = str(uuid.uuid4())[:8]
            logger.bind(
                request_id=request_id,
                error_id=error_id,
                error=str(e)
            ).exception(f"Query processing error")
            
            # Final fallback: return helpful message
            return {
                "success": False,
                "response": self._get_fallback_response(question),
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
            "version": "42.0",
            "architecture": "ai_first_fallback",
            "status": "healthy",
            "metrics": self.metrics.get_metrics(),
            "cache": self.cache.get_stats(),
            "context_stats": self.lru_context.get_stats(),
            "redis_available": self.redis_context.available,
            "service_health": self.service_health,
            "principles": [
                "Never Fail",
                "Always Answer", 
                "AI-First Fallback",
                "No Feature Under Development"
            ]
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
    global _SERVICE_INSTANCE
    if _SERVICE_INSTANCE is None:
        _SERVICE_INSTANCE = AIQueryService(session_factory)
    elif session_factory and _SERVICE_INSTANCE._session_factory is None:
        _SERVICE_INSTANCE._session_factory = session_factory
    return _SERVICE_INSTANCE


def process_whatsapp_query(question: str, session_factory, phone_number: str = None, 
                           user_id: str = None, request_id: str = None) -> str:
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
        return {"service": "ai_query_service", "status": "unhealthy", "error": str(e), "version": "42.0"}


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("🧠 AI QUERY SERVICE v42.0 - AI-FIRST ARCHITECTURE")
logger.info("")
logger.info("   CORE PRINCIPLES:")
logger.info("   ✅ AI-FIRST FALLBACK - Always try AI before showing errors")
logger.info("   ✅ NO 'FEATURE UNDER DEVELOPMENT' - Never shown to users")
logger.info("   ✅ UNIVERSAL AI MODE - All intents can use AI understanding")
logger.info("   ✅ SMART FALLBACK CHAIN - Route → AI → Help Menu")
logger.info("   ✅ SERVICE ISOLATION - Service failures never break the bot")
logger.info("   ✅ CONVERSATION MEMORY - Context-aware responses")
logger.info("   ✅ SELF-HEALING - Graceful degradation")
logger.info("")
logger.info("   WHATSAPP RESPONSES NOW WORK FOR:")
logger.info("   • 'How to fix the issue?'")
logger.info("   • 'Can you help me?'")
logger.info("   • 'What should we do?'")
logger.info("   • 'Why is Lahore delayed?'")
logger.info("   • 'What issue is coming?'")
logger.info("   • 'Any risks today?'")
logger.info("=" * 70)
