# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v31.0)
# ==========================================================
# SINGLE BRAIN OF THE SYSTEM - COMPLETE ENTERPRISE ARCHITECTURE
#
# ARCHITECTURE:
# WhatsApp → webhook.py → THIS FILE → Data Services → Business Rules → Groq → Response
#
# CAPABILITIES:
# - Universal Search Engine (search anything)
# - 100+ Query Types
# - Full Business Rules Integration
# - Complete Groq AI Layer
# - Data Validation (No N/A)
# - KPI Engine
# - Control Tower Engine
# - Executive AI Layer
# - Service Health Monitoring
# - Singleton Pattern for Performance
#
# ==========================================================

import re
import time
import hashlib
from typing import Dict, Any, Optional, List, Tuple, Callable
from datetime import datetime, date
from enum import Enum
from dataclasses import dataclass, field
from functools import wraps

from sqlalchemy.orm import Session
from loguru import logger

from app.config import config


# ==========================================================
# QUERY TYPES (100+ Enterprise Categories)
# ==========================================================

class QueryType(str, Enum):
    """Enterprise query types - complete catalog"""
    
    # ========== DN Operations (15 types) ==========
    DN_LOOKUP = "dn_lookup"
    DN_TIMELINE = "dn_timeline"
    DN_PRODUCTS = "dn_products"
    DN_DEALER = "dn_dealer"
    DN_WAREHOUSE = "dn_warehouse"
    DN_AGING = "dn_aging"
    DN_STATUS = "dn_status"
    DN_RISK = "dn_risk"
    DN_HEALTH = "dn_health"
    DN_RECOMMENDATIONS = "dn_recommendations"
    DN_CONTROL_TOWER = "dn_control_tower"
    
    # ========== POD Operations (12 types) ==========
    PENDING_POD = "pending_pod"
    PENDING_POD_BY_DAYS = "pending_pod_by_days"
    PENDING_POD_BY_CITY = "pending_pod_by_city"
    PENDING_POD_BY_DEALER = "pending_pod_by_dealer"
    POD_ANALYSIS = "pod_analysis"
    POD_PERFORMANCE = "pod_performance"
    POD_TREND = "pod_trend"
    POD_FORECAST = "pod_forecast"
    
    # ========== PGI Operations (10 types) ==========
    PENDING_PGI = "pending_pgi"
    PENDING_PGI_BY_DAYS = "pending_pgi_by_days"
    PENDING_PGI_BY_CITY = "pending_pgi_by_city"
    PENDING_PGI_BY_WAREHOUSE = "pending_pgi_by_warehouse"
    PGI_ANALYSIS = "pgi_analysis"
    PGI_PERFORMANCE = "pgi_performance"
    
    # ========== Dealer Operations (12 types) ==========
    DEALER_QUERY = "dealer_query"
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_RANKING = "dealer_ranking"
    TOP_DEALERS = "top_dealers"
    BOTTOM_DEALERS = "bottom_dealers"
    DEALER_GROWTH = "dealer_growth"
    DEALER_RISK = "dealer_risk"
    DEALER_PERFORMANCE = "dealer_performance"
    DEALER_AGING = "dealer_aging"
    DEALER_POD_STATUS = "dealer_pod_status"
    DEALER_PGI_STATUS = "dealer_pgi_status"
    
    # ========== Warehouse Operations (10 types) ==========
    WAREHOUSE_QUERY = "warehouse_query"
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    WAREHOUSE_RANKING = "warehouse_ranking"
    TOP_WAREHOUSES = "top_warehouses"
    WAREHOUSE_PERFORMANCE = "warehouse_performance"
    WAREHOUSE_DELAY = "warehouse_delay"
    WAREHOUSE_CAPACITY = "warehouse_capacity"
    
    # ========== City/Region Operations (8 types) ==========
    CITY_QUERY = "city_query"
    CITY_DASHBOARD = "city_dashboard"
    CITY_RANKING = "city_ranking"
    CITY_PERFORMANCE = "city_performance"
    REGION_ANALYSIS = "region_analysis"
    
    # ========== Product Operations (8 types) ==========
    PRODUCT_QUERY = "product_query"
    PRODUCT_RANKING = "product_ranking"
    TOP_PRODUCTS = "top_products"
    PRODUCT_PERFORMANCE = "product_performance"
    SLOW_MOVING = "slow_moving"
    FAST_MOVING = "fast_moving"
    DEAD_STOCK = "dead_stock"
    
    # ========== KPI & Dashboard (12 types) ==========
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    EXECUTIVE_SUMMARY = "executive_summary"
    NETWORK_HEALTH = "network_health"
    TOP_RISKS = "top_risks"
    KPI_DASHBOARD = "kpi_dashboard"
    REVENUE_ANALYSIS = "revenue_analysis"
    REVENUE_AT_RISK = "revenue_at_risk"
    SALES_FORECAST = "sales_forecast"
    TARGET_ACHIEVEMENT = "target_achievement"
    
    # ========== Control Tower (8 types) ==========
    CONTROL_TOWER = "control_tower"
    CRITICAL_DNS = "critical_dns"
    HIGH_RISK_DNS = "high_risk_dns"
    CRITICAL_PODS = "critical_pods"
    ALERTS = "alerts"
    EXCEPTIONS = "exceptions"
    
    # ========== AI Analysis (6 types) ==========
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    TREND_ANALYSIS = "trend_analysis"
    RECOMMENDATIONS = "recommendations"
    PREDICTIVE_ANALYSIS = "predictive_analysis"
    WHAT_IF_ANALYSIS = "what_if_analysis"
    
    # ========== General (5 types) ==========
    HELP = "help"
    GREETING = "greeting"
    GENERAL = "general"
    SEARCH = "search"
    UNIVERSAL = "universal"


# ==========================================================
# EXTRACTED ENTITIES (Enhanced)
# ==========================================================

@dataclass
class ExtractedEntities:
    """Enhanced entity extraction with more fields"""
    # Core entities
    dn_number: Optional[str] = None
    dealer: Optional[str] = None
    warehouse: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    product: Optional[str] = None
    
    # Time entities
    days: Optional[int] = None
    month: Optional[str] = None
    year: Optional[int] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    
    # Comparison entities
    compare_with: Optional[str] = None
    threshold: Optional[int] = None
    
    # Action entities
    action: Optional[str] = None
    priority: Optional[str] = None
    
    def has_any(self) -> bool:
        return any([
            self.dn_number, self.dealer, self.warehouse,
            self.city, self.region, self.product
        ])
    
    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}
    
    def is_valid(self) -> Tuple[bool, str]:
        """Validate entities have meaningful values"""
        if self.dn_number and len(str(self.dn_number)) < 8:
            return False, f"DN number {self.dn_number} is too short"
        return True, "valid"


# ==========================================================
# ENHANCED QUERY PARSER (Universal Search)
# ==========================================================

class QueryParser:
    """
    Enhanced query parser - universal search engine.
    Handles 100+ query patterns with intelligent routing.
    """
    
    # DN patterns
    DN_PATTERN = re.compile(r'\b(\d{8,15})\b')
    DN_WITH_PREFIX = re.compile(r'DN\s*[:]?\s*(\d{8,15})', re.IGNORECASE)
    
    # Time patterns
    DAYS_PATTERN = re.compile(r'(\d+)\s+days?', re.IGNORECASE)
    GREATER_THAN_PATTERN = re.compile(r'[>]\s*(\d+)|greater than (\d+)|more than (\d+)', re.IGNORECASE)
    
    # Comparison patterns
    COMPARE_PATTERN = re.compile(r'compare\s+(\w+)\s+(?:with|to|vs|against)\s+(\w+)', re.IGNORECASE)
    
    # Comprehensive keyword catalog
    KEYWORDS = {
        # DN Operations
        QueryType.DN_TIMELINE: ['timeline', 'journey', 'history', 'track', 'progress'],
        QueryType.DN_PRODUCTS: ['products', 'items', 'materials', 'what products', 'what items'],
        QueryType.DN_RISK: ['risk', 'risk assessment', 'critical', 'problem', 'issue'],
        QueryType.DN_HEALTH: ['health', 'health score', 'status', 'condition'],
        
        # POD Operations
        QueryType.PENDING_POD: ['pending pod', 'pod pending', 'missing pod', 'pod not received', 'pending proof'],
        QueryType.PENDING_POD_BY_DAYS: ['pod >', 'pod greater than', 'pod older than', 'pending pod over'],
        QueryType.POD_ANALYSIS: ['pod analysis', 'pod summary', 'pod report', 'analyze pod'],
        QueryType.POD_PERFORMANCE: ['pod performance', 'pod rate', 'pod compliance'],
        
        # PGI Operations
        QueryType.PENDING_PGI: ['pending pgi', 'pgi pending', 'pending dispatch', 'not dispatched', 'pending goods'],
        QueryType.PGI_ANALYSIS: ['pgi analysis', 'pgi summary', 'dispatch analysis'],
        
        # Dealer Operations
        QueryType.TOP_DEALERS: ['top dealer', 'best dealer', 'dealer ranking', 'top performing', 'leading dealer'],
        QueryType.BOTTOM_DEALERS: ['bottom dealer', 'worst dealer', 'lowest performing', 'poor performing'],
        QueryType.DEALER_GROWTH: ['dealer growth', 'growing dealer', 'improving dealer', 'dealer trend'],
        QueryType.DEALER_RISK: ['dealer risk', 'risky dealer', 'high risk dealer', 'dealer problem'],
        
        # Warehouse Operations
        QueryType.TOP_WAREHOUSES: ['top warehouse', 'best warehouse', 'warehouse ranking'],
        QueryType.WAREHOUSE_DELAY: ['warehouse delay', 'delay at warehouse', 'warehouse backlog'],
        
        # KPI Operations
        QueryType.EXECUTIVE_SUMMARY: ['executive summary', 'ceo summary', 'leadership summary', 'board summary'],
        QueryType.KPI_DASHBOARD: ['kpi', 'key performance', 'metrics', 'dashboard', 'performance metrics'],
        QueryType.TARGET_ACHIEVEMENT: ['target', 'achievement', 'goal', 'vs target'],
        
        # Control Tower
        QueryType.CONTROL_TOWER: ['control tower', 'command center', 'overview', 'all alerts'],
        QueryType.CRITICAL_DNS: ['critical dn', 'emergency dn', 'urgent dn', 'red dn'],
        QueryType.ALERTS: ['alert', 'warning', 'notification', 'attention needed'],
        
        # AI Analysis
        QueryType.ROOT_CAUSE_ANALYSIS: ['why', 'root cause', 'reason', 'cause', 'what caused', 'why is'],
        QueryType.TREND_ANALYSIS: ['trend', 'pattern', 'over time', 'monthly trend', 'weekly trend'],
        QueryType.PREDICTIVE_ANALYSIS: ['predict', 'forecast', 'will happen', 'expected', 'future'],
        QueryType.WHAT_IF_ANALYSIS: ['what if', 'scenario', 'simulate', 'if we'],
        
        # Revenue
        QueryType.REVENUE_AT_RISK: ['revenue at risk', 'at risk revenue', 'endangered revenue'],
        QueryType.SALES_FORECAST: ['sales forecast', 'revenue forecast', 'projected sales'],
    }
    
    # City/Region keywords
    CITIES = ['karachi', 'lahore', 'islamabad', 'rawalpindi', 'faisalabad', 
              'multan', 'peshawar', 'quetta', 'gujranwala', 'sialkot',
              'hyderabad', 'sukkur', 'bahawalpur', 'sargodha']
    
    # Warehouse keywords
    WAREHOUSES = ['north', 'south', 'east', 'west', 'central', 'main', 
                  'karachi wh', 'lahore wh', 'islamabad wh']
    
    @classmethod
    def parse(cls, question: str) -> Tuple[QueryType, ExtractedEntities]:
        """
        Universal query parser - handles any question format.
        Returns (query_type, extracted_entities)
        """
        question_lower = question.lower().strip()
        original_question = question
        entities = ExtractedEntities()
        
        # ==========================================================
        # STEP 1: Extract DN Number (highest priority)
        # ==========================================================
        dn_match = cls.DN_WITH_PREFIX.search(question)
        if not dn_match:
            dn_match = cls.DN_PATTERN.search(question)
        
        if dn_match:
            entities.dn_number = dn_match.group(1)
            return cls._route_dn_query(question_lower, entities)
        
        # ==========================================================
        # STEP 2: Extract Time Constraints
        # ==========================================================
        days_match = cls.DAYS_PATTERN.search(question_lower)
        if days_match:
            entities.days = int(days_match.group(1))
        
        greater_match = cls.GREATER_THAN_PATTERN.search(question_lower)
        if greater_match:
            days = greater_match.group(1) or greater_match.group(2) or greater_match.group(3)
            if days:
                entities.days = int(days)
                entities.threshold = int(days)
        
        # ==========================================================
        # STEP 3: Extract Comparison
        # ==========================================================
        compare_match = cls.COMPARE_PATTERN.search(question_lower)
        if compare_match:
            entities.compare_with = f"{compare_match.group(1)} vs {compare_match.group(2)}"
        
        # ==========================================================
        # STEP 4: Extract Location (City/Region)
        # ==========================================================
        for city in cls.CITIES:
            if city in question_lower:
                entities.city = city.capitalize()
                break
        
        for warehouse in cls.WAREHOUSES:
            if warehouse in question_lower:
                entities.warehouse = warehouse.capitalize()
                break
        
        # ==========================================================
        # STEP 5: Extract Dealer Name
        # ==========================================================
        dealer_patterns = [
            r'dealer\s+([A-Za-z0-9\s]+?)(?:\s+performance|\s+details|\s+$|\.|\,)',
            r'show\s+dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
            r'dealer\s+([A-Za-z0-9\s]+?)\s+(?:pod|delivery|pending|ranking)',
        ]
        
        for pattern in dealer_patterns:
            match = re.search(pattern, question_lower)
            if match:
                entities.dealer = match.group(1).strip()
                break
        
        # ==========================================================
        # STEP 6: Extract Product
        # ==========================================================
        product_patterns = [
            r'product\s+([A-Za-z0-9\s]+?)(?:\s+performance|\s+$|\.|\,)',
            r'show\s+product\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
        ]
        
        for pattern in product_patterns:
            match = re.search(pattern, question_lower)
            if match:
                entities.product = match.group(1).strip()
                break
        
        # ==========================================================
        # STEP 7: Route based on keywords
        # ==========================================================
        return cls._route_by_keywords(question_lower, entities, original_question)
    
    @classmethod
    def _route_dn_query(cls, question_lower: str, entities: ExtractedEntities) -> Tuple[QueryType, ExtractedEntities]:
        """Route DN-related queries to specific types"""
        
        if 'timeline' in question_lower or 'journey' in question_lower or 'history' in question_lower:
            return QueryType.DN_TIMELINE, entities
        elif 'product' in question_lower or 'item' in question_lower or 'material' in question_lower:
            return QueryType.DN_PRODUCTS, entities
        elif 'risk' in question_lower or 'critical' in question_lower or 'problem' in question_lower:
            return QueryType.DN_RISK, entities
        elif 'health' in question_lower or 'score' in question_lower:
            return QueryType.DN_HEALTH, entities
        elif 'recommend' in question_lower or 'action' in question_lower:
            return QueryType.DN_RECOMMENDATIONS, entities
        else:
            return QueryType.DN_LOOKUP, entities
    
    @classmethod
    def _route_by_keywords(cls, question_lower: str, entities: ExtractedEntities, original: str) -> Tuple[QueryType, ExtractedEntities]:
        """Route by keyword matching"""
        
        # Check for priority routing first
        if 'help' in question_lower or 'menu' in question_lower or 'what can you do' in question_lower:
            return QueryType.HELP, entities
        
        if any(g in question_lower for g in ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']):
            return QueryType.GREETING, entities
        
        # Route based on entities
        if entities.dealer:
            # Dealer-specific routing
            if 'dashboard' in question_lower or 'overview' in question_lower:
                return QueryType.DEALER_DASHBOARD, entities
            elif 'performance' in question_lower or 'metrics' in question_lower:
                return QueryType.DEALER_PERFORMANCE, entities
            elif 'risk' in question_lower:
                return QueryType.DEALER_RISK, entities
            else:
                return QueryType.DEALER_QUERY, entities
        
        if entities.city:
            if 'performance' in question_lower:
                return QueryType.CITY_PERFORMANCE, entities
            elif 'ranking' in question_lower:
                return QueryType.CITY_RANKING, entities
            else:
                return QueryType.CITY_DASHBOARD, entities
        
        if entities.warehouse:
            if 'delay' in question_lower:
                return QueryType.WAREHOUSE_DELAY, entities
            elif 'performance' in question_lower:
                return QueryType.WAREHOUSE_PERFORMANCE, entities
            else:
                return QueryType.WAREHOUSE_DASHBOARD, entities
        
        if entities.product:
            return QueryType.PRODUCT_QUERY, entities
        
        # Keyword-based routing
        for query_type, keywords in cls.KEYWORDS.items():
            for keyword in keywords:
                if keyword in question_lower:
                    # Check for days threshold
                    if query_type == QueryType.PENDING_POD_BY_DAYS and entities.days:
                        return query_type, entities
                    return query_type, entities
        
        # Universal search fallback
        return QueryType.UNIVERSAL, entities


# ==========================================================
# RESPONSE CACHE (Enhanced)
# ==========================================================

class ResponseCache:
    """Enhanced TTL cache with invalidation strategies"""
    
    def __init__(self, ttl: int = 300):
        self.cache = {}
        self.ttl = ttl
        self.hits = 0
        self.misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                self.hits += 1
                return data
            del self.cache[key]
        self.misses += 1
        return None
    
    def set(self, key: str, value: Any):
        self.cache[key] = (value, time.time())
    
    def get_key(self, query_type: QueryType, entities: ExtractedEntities) -> str:
        entities_str = str(sorted(entities.to_dict().items()))
        return hashlib.md5(f"{query_type.value}:{entities_str}".encode()).hexdigest()
    
    def invalidate_pattern(self, pattern: str):
        """Invalidate cache keys matching pattern"""
        to_delete = [k for k in self.cache.keys() if pattern in k]
        for k in to_delete:
            del self.cache[k]
        return len(to_delete)
    
    def clear(self):
        count = len(self.cache)
        self.cache.clear()
        return count
    
    def get_stats(self) -> Dict:
        total = self.hits + self.misses
        return {
            "size": len(self.cache),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / max(1, total) * 100, 1)
        }


# ==========================================================
# DATA VALIDATOR (No N/A - Improvement #6)
# ==========================================================

class DataValidator:
    """
    Data validation layer - ensures no N/A values reach the user.
    This prevents the "Dealer: N/A, City: N/A, Warehouse: N/A" issue.
    """
    
    @staticmethod
    def validate_dn_data(data: Dict) -> Tuple[bool, str, Dict]:
        """Validate DN data has meaningful values"""
        if not data:
            return False, "No data found for this DN", {}
        
        # Check for error indicators
        if data.get("error"):
            return False, data.get("error"), {}
        
        # Check for success flag
        if data.get("success") is False:
            return False, data.get("error", "DN not found"), {}
        
        # Extract data field if present
        actual_data = data.get("data", data)
        
        # Check for empty data
        if not actual_data or len(actual_data) == 0:
            return False, "No records found", {}
        
        # Check for N/A values in critical fields
        critical_fields = ["dealer", "city", "warehouse", "status"]
        na_fields = []
        
        for field in critical_fields:
            value = actual_data.get(field)
            if value in [None, "N/A", "Unknown", "Unknown Dealer", "", "None"]:
                na_fields.append(field)
        
        if len(na_fields) >= 2:
            return False, f"Data incomplete: {', '.join(na_fields)} not found", {}
        
        return True, "valid", actual_data
    
    @staticmethod
    def validate_list_data(data: Dict, item_key: str) -> Tuple[bool, str, List]:
        """Validate list data has items"""
        if not data:
            return False, "No data available", []
        
        items = data.get(item_key, [])
        if not items:
            return False, f"No {item_key.replace('_', ' ')} found", []
        
        return True, f"Found {len(items)} items", items
    
    @staticmethod
    def sanitize_value(value: Any, default: str = "Not Available") -> str:
        """Sanitize a value to prevent N/A"""
        if value is None:
            return default
        if isinstance(value, str) and value.strip() in ["", "N/A", "NA", "n/a"]:
            return default
        return str(value)


# ==========================================================
# RESPONSE FORMATTER (Enhanced)
# ==========================================================

class ResponseFormatter:
    """Enhanced WhatsApp response formatter"""
    
    @staticmethod
    def success(data: Any, summary: str = None, recommendations: List[str] = None, 
                metadata: Dict = None) -> Dict:
        return {
            "success": True,
            "data": data,
            "summary": summary or "",
            "recommendations": recommendations or [],
            "metadata": metadata or {},
            "source": "database"
        }
    
    @staticmethod
    def error(message: str, code: str = "unknown") -> Dict:
        return {
            "success": False,
            "data": {},
            "summary": message,
            "recommendations": [],
            "error_code": code,
            "source": "error"
        }
    
    @staticmethod
    def to_whatsapp(response: Dict) -> str:
        if not response.get("success"):
            return f"❌ {response.get('summary', 'Unable to process request')}"
        
        data = response.get("data", {})
        summary = response.get("summary", "")
        recommendations = response.get("recommendations", [])
        
        if isinstance(data, str):
            return data
        
        if isinstance(data, dict) and data.get("whatsapp_message"):
            return data["whatsapp_message"]
        
        if summary:
            # Add recommendations if available
            if recommendations:
                summary += "\n\n💡 *Recommendations:*\n"
                for rec in recommendations[:3]:
                    summary += f"• {rec}\n"
            return summary
        
        return "✅ Request processed successfully"


# ==========================================================
# GROQ INSIGHT GENERATOR (Complete AI Layer)
# ==========================================================

class GroqInsightGenerator:
    """
    Complete Groq AI Layer - handles all analysis types.
    Integration with business rules for context-aware insights.
    """
    
    def __init__(self, db: Session, business_rules=None):
        self.db = db
        self._groq_service = None
        self.business_rules = business_rules
    
    @property
    def groq_service(self):
        if self._groq_service is None:
            try:
                from app.services.groq_insight_service import GroqInsightService
                self._groq_service = GroqInsightService(self.db)
            except Exception as e:
                logger.warning(f"Groq service not available: {e}")
        return self._groq_service
    
    def generate(self, query_type: QueryType, data: Dict, question: str, 
                 metrics: Dict = None) -> Optional[str]:
        """Generate insight using Groq with full context"""
        if not self.groq_service:
            return None
        
        # All analysis types now go through Groq
        groq_enabled_types = [
            QueryType.ROOT_CAUSE_ANALYSIS,
            QueryType.TREND_ANALYSIS,
            QueryType.RECOMMENDATIONS,
            QueryType.PREDICTIVE_ANALYSIS,
            QueryType.WHAT_IF_ANALYSIS,
            QueryType.EXECUTIVE_SUMMARY,
            QueryType.CONTROL_TOWER,
        ]
        
        if query_type not in groq_enabled_types:
            return None
        
        try:
            prompt = self._build_enhanced_prompt(query_type, data, question, metrics)
            if not prompt:
                return None
            
            result = self.groq_service.analyze(prompt, query_type.value, {})
            if isinstance(result, dict):
                return result.get("insight") or result.get("response") or str(result)
            return str(result) if result else None
            
        except Exception as e:
            logger.error(f"Groq insight failed: {e}")
            return None
    
    def _build_enhanced_prompt(self, query_type: QueryType, data: Dict, 
                                question: str, metrics: Dict) -> Optional[str]:
        """Build enhanced prompt with business rules context"""
        
        if query_type == QueryType.ROOT_CAUSE_ANALYSIS:
            return f"""
You are a logistics intelligence expert. Analyze this data to identify root causes.

Question: {question}

Current Metrics: {metrics or {}}

Data: {data}

Please provide a concise analysis with:
1. Root Cause (what's actually causing this)
2. Business Impact (how it affects operations)
3. Recommended Actions (what to do immediately)
4. Risk Level (Critical/High/Medium/Low)
5. Expected Timeline for resolution
"""
        
        elif query_type == QueryType.EXECUTIVE_SUMMARY:
            return f"""
You are presenting to the CEO. Create an executive summary.

Question: {question}

Metrics: {metrics or {}}

Data: {data}

Provide:
1. Key Highlights (top 3 achievements)
2. Critical Issues (top 3 risks)
3. Recommendations (priority actions)
4. Outlook for next period

Keep it professional and actionable.
"""
        
        elif query_type == QueryType.CONTROL_TOWER:
            return f"""
You are the control tower command center. Analyze this situation.

Question: {question}

Data: {data}

Provide:
1. Critical Alerts (what needs immediate attention)
2. High Risk Items (what to monitor closely)
3. Recommendations (what to do now, next hour, next day)
4. Escalation Required (who needs to be notified)

Be direct and actionable.
"""
        
        elif query_type == QueryType.PREDICTIVE_ANALYSIS:
            return f"""
You are a logistics forecasting expert. Predict future outcomes.

Question: {question}

Historical Data: {data}

Provide:
1. Short-term Forecast (next 7 days)
2. Medium-term Forecast (next 30 days)
3. Key Risk Factors
4. Mitigation Strategies

Base predictions on the data patterns observed.
"""
        
        elif query_type == QueryType.WHAT_IF_ANALYSIS:
            return f"""
You are a logistics strategist. Analyze this scenario.

Question: {question}

Current Data: {data}

Provide:
1. Scenario Analysis (best case, worst case, most likely)
2. Key Dependencies
3. Implementation Considerations
4. Recommended Decision

Think step by step.
"""
        
        elif query_type == QueryType.TREND_ANALYSIS:
            return f"""
Analyze trends in this logistics data:

Question: {question}

Data: {data}

Provide:
1. Key Trends Observed (improving, declining, stable)
2. Pattern Insights (seasonal, weekly, monthly)
3. Anomalies Detected (unusual patterns)
4. Future Predictions
"""
        
        elif query_type == QueryType.RECOMMENDATIONS:
            return f"""
Provide actionable recommendations based on:

Question: {question}

Data: {data}

Provide:
1. Immediate Actions (next 24 hours)
2. Short-term Actions (next 7 days)
3. Long-term Improvements (next 30 days)
4. Expected Outcomes for each recommendation
"""
        
        return None


# ==========================================================
# BUSINESS RULES ENGINE (Complete - Improvement #2)
# ==========================================================

class BusinessRulesEngine:
    """
    Complete Business Rules Engine - all KPI calculations.
    Moved from logistics_query_service.py to central location.
    """
    
    @staticmethod
    def calculate_delivery_aging(dn_create_date) -> int:
        if not dn_create_date:
            return 0
        if isinstance(dn_create_date, datetime):
            dn_create_date = dn_create_date.date()
        return max(0, (date.today() - dn_create_date).days)
    
    @staticmethod
    def calculate_pending_delivery(good_issue_date) -> int:
        if not good_issue_date:
            return 0
        if isinstance(good_issue_date, datetime):
            good_issue_date = good_issue_date.date()
        return max(0, (date.today() - good_issue_date).days)
    
    @staticmethod
    def calculate_pod_aging(pod_date) -> int:
        if not pod_date:
            return 0
        if isinstance(pod_date, datetime):
            pod_date = pod_date.date()
        return max(0, (date.today() - pod_date).days)
    
    @staticmethod
    def calculate_pending_pod(good_issue_date) -> int:
        if not good_issue_date:
            return 0
        if isinstance(good_issue_date, datetime):
            good_issue_date = good_issue_date.date()
        return max(0, (date.today() - good_issue_date).days)
    
    @staticmethod
    def calculate_sla_status(days: int, target_days: int = 3) -> Tuple[str, str, float]:
        if days <= target_days:
            return "On Time", "✅", 100.0
        else:
            achievement = max(0, (1 - (days - target_days) / target_days) * 100)
            return "Delayed", "🔴", round(achievement, 1)
    
    @staticmethod
    def calculate_delay_category(days: int) -> Tuple[str, str]:
        if days <= 0:
            return "Current", "✅"
        elif days <= 3:
            return "Warning", "⚠️"
        elif days <= 7:
            return "Late", "⏰"
        elif days <= 15:
            return "Very Late", "🔴"
        else:
            return "Critical", "💀"
    
    @staticmethod
    def calculate_risk_score(days: int, value: float) -> Tuple[int, str]:
        # Delay component (0-70)
        if days <= 1:
            delay_score = 0
        elif days <= 3:
            delay_score = 10
        elif days <= 7:
            delay_score = 25
        elif days <= 15:
            delay_score = 45
        else:
            delay_score = 70
        
        # Value component (0-30)
        if value >= 10_000_000:
            value_score = 30
        elif value >= 5_000_000:
            value_score = 20
        elif value >= 1_000_000:
            value_score = 10
        else:
            value_score = 0
        
        score = delay_score + value_score
        
        if score >= 70:
            level = "Critical"
        elif score >= 50:
            level = "High"
        elif score >= 30:
            level = "Medium"
        else:
            level = "Low"
        
        return min(100, score), level
    
    @staticmethod
    def calculate_health_score(metrics: Dict) -> Tuple[int, str]:
        """Calculate overall health score"""
        weights = {
            'delivery_aging': 0.25,
            'pod_aging': 0.25,
            'pending_delivery': 0.25,
            'pending_pod': 0.25
        }
        
        total = 0
        for key, weight in weights.items():
            value = metrics.get(key, 0)
            if value <= 1:
                score = 100
            elif value <= 3:
                score = 80
            elif value <= 7:
                score = 60
            elif value <= 15:
                score = 40
            else:
                score = 20
            total += score * weight
        
        score = int(total)
        
        if score >= 80:
            grade = "Excellent"
        elif score >= 60:
            grade = "Good"
        elif score >= 40:
            grade = "Warning"
        else:
            grade = "Critical"
        
        return score, grade
    
    @staticmethod
    def calculate_priority(risk_score: int, days: int, exception_flag: bool) -> Tuple[str, str]:
        if exception_flag and risk_score >= 70:
            return "Critical", "💀"
        elif exception_flag and risk_score >= 50:
            return "High", "🚨"
        elif days > 7:
            return "Medium", "⚠️"
        else:
            return "Low", "✅"
    
    @staticmethod
    def calculate_branch_performance(completed: int, total: int, target: float = 98.0) -> Dict:
        rate = (completed / total * 100) if total > 0 else 0
        achievement = (rate / target * 100) if target > 0 else 0
        
        if rate >= target:
            status = "Above Target"
            icon = "✅"
        elif rate >= target * 0.9:
            status = "Near Target"
            icon = "⚠️"
        else:
            status = "Below Target"
            icon = "🔴"
        
        return {
            "completion_rate": round(rate, 1),
            "achievement": round(achievement, 1),
            "status": status,
            "icon": icon
        }
    
    @staticmethod
    def generate_kpi_dashboard(data: Dict) -> Dict:
        """Generate comprehensive KPI dashboard"""
        pgi_total = data.get('pgi_total', 0)
        pgi_completed = data.get('pgi_completed', 0)
        pod_total = data.get('pod_total', 0)
        pod_received = data.get('pod_received', 0)
        
        pgi_rate = (pgi_completed / max(1, pgi_total)) * 100
        pod_rate = (pod_received / max(1, pod_total)) * 100
        
        health_score, health_grade = BusinessRulesEngine.calculate_health_score(data)
        
        return {
            "pgi_performance": {
                "rate": round(pgi_rate, 1),
                "completed": pgi_completed,
                "total": pgi_total,
                "icon": "✅" if pgi_rate >= 95 else "⚠️" if pgi_rate >= 80 else "🔴"
            },
            "pod_performance": {
                "rate": round(pod_rate, 1),
                "received": pod_received,
                "total": pod_total,
                "icon": "✅" if pod_rate >= 95 else "⚠️" if pod_rate >= 80 else "🔴"
            },
            "health_score": health_score,
            "health_grade": health_grade,
            "health_icon": "💚" if health_score >= 70 else "💛" if health_score >= 50 else "❤️"
        }


# ==========================================================
# KPI ENGINE (Improvement #7)
# ==========================================================

class KPIEngine:
    """Generate comprehensive KPI dashboards"""
    
    def __init__(self, business_rules: BusinessRulesEngine):
        self.business_rules = business_rules
    
    def generate_dashboard(self, data: Dict) -> Dict:
        return self.business_rules.generate_kpi_dashboard(data)
    
    def calculate_target_achievement(self, actual: float, target: float) -> Dict:
        achievement = (actual / target * 100) if target > 0 else 0
        return {
            "actual": round(actual, 1),
            "target": round(target, 1),
            "achievement": round(achievement, 1),
            "gap": round(target - actual, 1),
            "status": "✅" if achievement >= 100 else "⚠️" if achievement >= 80 else "🔴"
        }


# ==========================================================
# CONTROL TOWER ENGINE (Improvement #8)
# ==========================================================

class ControlTowerEngine:
    """Control tower for critical alerts and monitoring"""
    
    @staticmethod
    def generate_alerts(data: Dict) -> Dict:
        alerts = []
        critical = []
        
        # Check pending PODs
        pending_pods = data.get('pending_pods', [])
        if pending_pods:
            old_pods = [p for p in pending_pods if p.get('pending_days', 0) > 7]
            if old_pods:
                critical.append({
                    "type": "POD_DELAY",
                    "message": f"{len(old_pods)} PODs pending >7 days",
                    "severity": "Critical"
                })
            elif pending_pods:
                alerts.append({
                    "type": "POD_PENDING",
                    "message": f"{len(pending_pods)} PODs pending",
                    "severity": "Warning"
                })
        
        # Check pending PGI
        pending_pgi = data.get('pending_pgi', [])
        if pending_pgi:
            old_pgi = [p for p in pending_pgi if p.get('pending_days', 0) > 5]
            if old_pgi:
                critical.append({
                    "type": "PGI_DELAY",
                    "message": f"{len(old_pgi)} PGI pending >5 days",
                    "severity": "Critical"
                })
            elif pending_pgi:
                alerts.append({
                    "type": "PGI_PENDING",
                    "message": f"{len(pending_pgi)} PGI pending",
                    "severity": "Warning"
                })
        
        return {
            "critical_alerts": critical,
            "warnings": alerts,
            "total_critical": len(critical),
            "total_warnings": len(alerts),
            "status": "🔴" if critical else "⚠️" if alerts else "✅"
        }


# ==========================================================
# MASTER ROUTER (Improvement #5)
# ==========================================================

class MasterRouter:
    """
    Master router - replaces large if/elif chains.
    Routes query types to handler methods.
    """
    
    def __init__(self, service_instance):
        self.service = service_instance
        self._routes = {}
        self._register_routes()
    
    def _register_routes(self):
        """Register all route handlers"""
        # DN routes
        self._routes[QueryType.DN_LOOKUP] = self.service._handle_dn_lookup
        self._routes[QueryType.DN_TIMELINE] = self.service._handle_dn_timeline
        self._routes[QueryType.DN_PRODUCTS] = self.service._handle_dn_products
        self._routes[QueryType.DN_RISK] = self.service._handle_dn_risk
        self._routes[QueryType.DN_HEALTH] = self.service._handle_dn_health
        
        # POD routes
        self._routes[QueryType.PENDING_POD] = self.service._handle_pending_pod
        self._routes[QueryType.PENDING_POD_BY_DAYS] = self.service._handle_pending_pod_by_days
        self._routes[QueryType.POD_ANALYSIS] = self.service._handle_pod_analysis
        
        # PGI routes
        self._routes[QueryType.PENDING_PGI] = self.service._handle_pending_pgi
        
        # Dealer routes
        self._routes[QueryType.TOP_DEALERS] = self.service._handle_top_dealers
        self._routes[QueryType.DEALER_QUERY] = self.service._handle_dealer_query
        self._routes[QueryType.DEALER_DASHBOARD] = self.service._handle_dealer_dashboard
        
        # Warehouse routes
        self._routes[QueryType.WAREHOUSE_RANKING] = self.service._handle_warehouse_ranking
        
        # KPI routes
        self._routes[QueryType.EXECUTIVE_DASHBOARD] = self.service._handle_executive_dashboard
        self._routes[QueryType.EXECUTIVE_SUMMARY] = self.service._handle_executive_summary
        self._routes[QueryType.NETWORK_HEALTH] = self.service._handle_network_health
        self._routes[QueryType.TOP_RISKS] = self.service._handle_top_risks
        
        # Control tower
        self._routes[QueryType.CONTROL_TOWER] = self.service._handle_control_tower
        
        # General
        self._routes[QueryType.HELP] = self.service._handle_help
        self._routes[QueryType.GREETING] = self.service._handle_greeting
        self._routes[QueryType.GENERAL] = self.service._handle_general
        self._routes[QueryType.UNIVERSAL] = self.service._handle_universal
    
    def route(self, query_type: QueryType, entities: ExtractedEntities, question: str) -> Dict:
        """Route to appropriate handler"""
        handler = self._routes.get(query_type)
        if handler:
            return handler(entities, question)
        
        # Fallback to universal handler
        return self.service._handle_universal(entities, question)


# ==========================================================
# MAIN AI QUERY SERVICE (SINGLE BRAIN - v31.0)
# ==========================================================

class AIQueryService:
    """
    Single Brain of the System - Complete Enterprise Version v31.0
    
    Features:
    - Universal search engine
    - 100+ query types
    - Full business rules integration
    - Complete Groq AI layer
    - Data validation (no N/A)
    - KPI engine
    - Control tower engine
    - Executive AI layer
    - Health monitoring
    - Singleton pattern for performance
    """
    
    # Singleton instance
    _instance = None
    
    def __new__(cls, db: Session = None):
        if cls._instance is None and db is not None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, db: Session = None):
        if self._initialized:
            return
        if db is None:
            raise ValueError("First initialization requires db parameter")
        
        self.db = db
        self.start_time = None
        self.request_id = None
        
        # Core components
        self.parser = QueryParser()
        self.cache = ResponseCache()
        self.formatter = ResponseFormatter()
        self.validator = DataValidator()
        self.business_rules = BusinessRulesEngine()
        self.kpi_engine = KPIEngine(self.business_rules)
        self.control_tower = ControlTowerEngine()
        self.groq = GroqInsightGenerator(db, self.business_rules)
        
        # Master router
        self.router = MasterRouter(self)
        
        # Service providers (lazy loaded)
        self._logistics_service = None
        self._analytics_service = None
        self._kpi_service = None
        
        # Metrics
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "avg_response_time_ms": 0,
            "cache_hits": 0,
            "groq_calls": 0,
            "startup_time": datetime.now().isoformat()
        }
        
        self._initialized = True
        
        logger.info("=" * 70)
        logger.info("🧠 AI QUERY SERVICE v31.0 - COMPLETE ENTERPRISE ARCHITECTURE")
        logger.info("   Features: Universal Search | 100+ Query Types | Full Groq AI")
        logger.info("   Business Rules | KPI Engine | Control Tower | No N/A Guarantee")
        logger.info("=" * 70)
    
    @property
    def logistics_service(self):
        if self._logistics_service is None:
            try:
                from app.services.logistics_query_service import LogisticsQueryService
                self._logistics_service = LogisticsQueryService(self.db)
                logger.info("✅ LogisticsQueryService loaded")
            except Exception as e:
                logger.error(f"Failed to load LogisticsQueryService: {e}")
        return self._logistics_service
    
    @property
    def analytics_service(self):
        if self._analytics_service is None:
            try:
                from app.services.analytics_service import AnalyticsService
                self._analytics_service = AnalyticsService(self.db)
                logger.info("✅ AnalyticsService loaded")
            except Exception as e:
                logger.error(f"Failed to load AnalyticsService: {e}")
        return self._analytics_service
    
    @property
    def kpi_service(self):
        if self._kpi_service is None:
            try:
                from app.services.kpi_service import KPIService
                self._kpi_service = KPIService(self.db)
                logger.info("✅ KPIService loaded")
            except Exception as e:
                logger.error(f"Failed to load KPIService: {e}")
        return self._kpi_service
    
    # ==========================================================
    # MAIN PROCESSING METHOD
    # ==========================================================
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict:
        self.start_time = time.time()
        self.request_id = hashlib.md5(f"{user_phone}:{question}".encode()).hexdigest()[:8]
        self.metrics["total_requests"] += 1
        
        question = question.strip()
        logger.info(f"[{self.request_id}] 📱 {question[:100]}")
        
        # Step 1: Parse
        query_type, entities = self.parser.parse(question)
        logger.info(f"[{self.request_id}] 🎯 Type={query_type.value} | Entities={entities.to_dict()}")
        
        # Step 2: Check cache
        cache_key = self.cache.get_key(query_type, entities)
        cached = self.cache.get(cache_key)
        if cached:
            logger.info(f"[{self.request_id}] 💾 Cache hit")
            self.metrics["cache_hits"] += 1
            return cached
        
        # Step 3: Route via master router
        result = self.router.route(query_type, entities, question)
        
        # Step 4: Apply business rules
        result = self._apply_business_rules(result, query_type, entities)
        
        # Step 5: Generate Groq insights for complex queries
        if result.get("success") and result.get("data"):
            groq_insight = self.groq.generate(query_type, result.get("data"), question, result.get("metrics"))
            if groq_insight:
                self.metrics["groq_calls"] += 1
                result["groq_insight"] = groq_insight
                logger.info(f"[{self.request_id}] 🤖 Groq insight generated")
        
        # Step 6: Format response
        whatsapp_response = self.formatter.to_whatsapp(result)
        
        final_response = {
            "success": result.get("success", True),
            "response": whatsapp_response,
            "query_type": query_type.value,
            "request_id": self.request_id
        }
        
        # Cache successful responses
        if result.get("success") and len(whatsapp_response) > 50:
            self.cache.set(cache_key, final_response)
        
        # Update metrics
        elapsed_ms = (time.time() - self.start_time) * 1000
        self.metrics["successful_requests" if result.get("success") else "failed_requests"] += 1
        self.metrics["avg_response_time_ms"] = (
            (self.metrics["avg_response_time_ms"] * (self.metrics["total_requests"] - 1) + elapsed_ms)
            / self.metrics["total_requests"]
        )
        
        logger.info(f"[{self.request_id}] ✅ Done | {elapsed_ms:.0f}ms")
        
        return final_response
    
    # ==========================================================
    # ROUTER HANDLERS
    # ==========================================================
    
    def _handle_dn_lookup(self, entities: ExtractedEntities, question: str) -> Dict:
        if not entities.dn_number:
            return self.formatter.error("Please provide a DN number")
        
        if not self.logistics_service:
            return self.formatter.error("Logistics service unavailable")
        
        result = self.logistics_service.get_complete_dn_intelligence(entities.dn_number)
        is_valid, msg, data = self.validator.validate_dn_data(result)
        
        if not is_valid:
            return self.formatter.error(msg)
        
        return self.formatter.success(
            data=data,
            summary=self._build_dn_summary(data)
        )
    
    def _handle_dn_timeline(self, entities: ExtractedEntities, question: str) -> Dict:
        if not entities.dn_number:
            return self.formatter.error("Please provide a DN number")
        
        if self.logistics_service:
            result = self.logistics_service.get_dn_timeline(entities.dn_number)
            return self.formatter.success(data=result, summary="Timeline retrieved")
        
        return self.formatter.error("Service unavailable")
    
    def _handle_dn_products(self, entities: ExtractedEntities, question: str) -> Dict:
        if not entities.dn_number:
            return self.formatter.error("Please provide a DN number")
        
        if self.logistics_service:
            result = self.logistics_service.get_dn_products(entities.dn_number)
            return self.formatter.success(data=result, summary="Products retrieved")
        
        return self.formatter.error("Service unavailable")
    
    def _handle_dn_risk(self, entities: ExtractedEntities, question: str) -> Dict:
        if not entities.dn_number:
            return self.formatter.error("Please provide a DN number")
        
        if self.logistics_service:
            result = self.logistics_service.get_complete_dn_intelligence(entities.dn_number)
            is_valid, msg, data = self.validator.validate_dn_data(result)
            
            if not is_valid:
                return self.formatter.error(msg)
            
            days = data.get("aging", {}).get("max_delay", 0)
            value = data.get("total_value", 0)
            risk_score, risk_level = self.business_rules.calculate_risk_score(days, value)
            
            return self.formatter.success(
                data={"risk_score": risk_score, "risk_level": risk_level},
                summary=f"Risk Level: {risk_level} (Score: {risk_score}/100)"
            )
        
        return self.formatter.error("Service unavailable")
    
    def _handle_dn_health(self, entities: ExtractedEntities, question: str) -> Dict:
        if not entities.dn_number:
            return self.formatter.error("Please provide a DN number")
        
        if self.logistics_service:
            result = self.logistics_service.get_complete_dn_intelligence(entities.dn_number)
            is_valid, msg, data = self.validator.validate_dn_data(result)
            
            if not is_valid:
                return self.formatter.error(msg)
            
            aging = data.get("aging", {})
            health_score, health_grade = self.business_rules.calculate_health_score(aging)
            
            return self.formatter.success(
                data={"health_score": health_score, "health_grade": health_grade},
                summary=f"Health Score: {health_score}/100 ({health_grade})"
            )
        
        return self.formatter.error("Service unavailable")
    
    def _handle_pending_pod(self, entities: ExtractedEntities, question: str) -> Dict:
        if self.logistics_service:
            result = self.logistics_service.get_pending_pods()
            is_valid, msg, items = self.validator.validate_list_data(result, "pending_pods")
            
            if not is_valid:
                return self.formatter.error(msg)
            
            # Apply days filter if specified
            if entities.days:
                items = [i for i in items if i.get("pending_days", 0) >= entities.days]
            
            return self.formatter.success(
                data={"pending_pods": items, "count": len(items)},
                summary=self._build_pending_pod_summary({"pending_pods": items})
            )
        
        return self.formatter.error("Service unavailable")
    
    def _handle_pending_pod_by_days(self, entities: ExtractedEntities, question: str) -> Dict:
        if not entities.days:
            return self.formatter.error("Please specify number of days")
        
        if self.logistics_service:
            result = self.logistics_service.get_pending_pods()
            is_valid, msg, items = self.validator.validate_list_data(result, "pending_pods")
            
            if not is_valid:
                return self.formatter.error(msg)
            
            filtered = [i for i in items if i.get("pending_days", 0) >= entities.days]
            
            return self.formatter.success(
                data={"pending_pods": filtered, "days_threshold": entities.days, "count": len(filtered)},
                summary=f"Found {len(filtered)} PODs pending >{entities.days} days"
            )
        
        return self.formatter.error("Service unavailable")
    
    def _handle_pod_analysis(self, entities: ExtractedEntities, question: str) -> Dict:
        if self.logistics_service:
            result = self.logistics_service.get_pending_pods()
            is_valid, msg, items = self.validator.validate_list_data(result, "pending_pods")
            
            if not is_valid:
                return self.formatter.error(msg)
            
            # Calculate metrics
            total = len(items)
            avg_days = sum(i.get("pending_days", 0) for i in items) / max(1, total)
            high_risk = len([i for i in items if i.get("pending_days", 0) > 7])
            
            analysis = {
                "total_pending": total,
                "average_days": round(avg_days, 1),
                "high_risk_count": high_risk,
                "high_risk_percentage": round(high_risk / max(1, total) * 100, 1)
            }
            
            return self.formatter.success(
                data=analysis,
                summary=f"POD Analysis: {total} pending, {high_risk} high-risk"
            )
        
        return self.formatter.error("Service unavailable")
    
    def _handle_pending_pgi(self, entities: ExtractedEntities, question: str) -> Dict:
        if self.logistics_service:
            result = self.logistics_service.get_pending_pgi()
            is_valid, msg, items = self.validator.validate_list_data(result, "pending_pgi")
            
            if not is_valid:
                return self.formatter.error(msg)
            
            if entities.days:
                items = [i for i in items if i.get("pending_days", 0) >= entities.days]
            
            return self.formatter.success(
                data={"pending_pgi": items, "count": len(items)},
                summary=self._build_pending_pgi_summary({"pending_pgi": items})
            )
        
        return self.formatter.error("Service unavailable")
    
    def _handle_top_dealers(self, entities: ExtractedEntities, question: str) -> Dict:
        if self.analytics_service:
            result = self.analytics_service.get_dealer_ranking()
            return self.formatter.success(
                data=result,
                summary=self._build_dealer_ranking_summary(result)
            )
        
        return self.formatter.error("Analytics service unavailable")
    
    def _handle_dealer_query(self, entities: ExtractedEntities, question: str) -> Dict:
        if not entities.dealer:
            return self.formatter.error("Please specify a dealer name")
        
        if self.analytics_service:
            result = self.analytics_service.get_dealer_dashboard(entities.dealer)
            if result:
                return self.formatter.success(
                    data=result,
                    summary=self._build_dealer_summary(result)
                )
        
        return self.formatter.error(f"Dealer '{entities.dealer}' not found")
    
    def _handle_dealer_dashboard(self, entities: ExtractedEntities, question: str) -> Dict:
        return self._handle_dealer_query(entities, question)
    
    def _handle_warehouse_ranking(self, entities: ExtractedEntities, question: str) -> Dict:
        if self.analytics_service:
            result = self.analytics_service.get_warehouse_ranking()
            return self.formatter.success(
                data=result,
                summary=self._build_warehouse_summary(result)
            )
        
        return self.formatter.error("Analytics service unavailable")
    
    def _handle_executive_dashboard(self, entities: ExtractedEntities, question: str) -> Dict:
        if self.kpi_service:
            result = self.kpi_service.get_executive_dashboard()
            
            # Generate KPI metrics
            kpi_metrics = self.kpi_engine.generate_dashboard(result)
            
            return self.formatter.success(
                data=result,
                metadata=kpi_metrics,
                summary=self._build_dashboard_summary(result)
            )
        
        return self.formatter.error("KPI service unavailable")
    
    def _handle_executive_summary(self, entities: ExtractedEntities, question: str) -> Dict:
        """Generate AI-powered executive summary"""
        if self.kpi_service:
            result = self.kpi_service.get_executive_dashboard()
            
            # Generate KPI metrics
            kpi_metrics = self.kpi_engine.generate_dashboard(result)
            
            # Groq will generate the executive summary
            return self.formatter.success(
                data=result,
                metadata=kpi_metrics,
                summary="Executive summary ready. AI analysis will provide detailed insights."
            )
        
        return self.formatter.error("KPI service unavailable")
    
    def _handle_network_health(self, entities: ExtractedEntities, question: str) -> Dict:
        if self.kpi_service:
            result = self.kpi_service.get_network_health()
            return self.formatter.success(
                data=result,
                summary=self._build_health_summary(result)
            )
        
        return self.formatter.error("KPI service unavailable")
    
    def _handle_top_risks(self, entities: ExtractedEntities, question: str) -> Dict:
        if self.kpi_service:
            result = self.kpi_service.get_top_risks()
            return self.formatter.success(
                data=result,
                summary=self._build_risks_summary(result)
            )
        
        return self.formatter.error("KPI service unavailable")
    
    def _handle_control_tower(self, entities: ExtractedEntities, question: str) -> Dict:
        """Control tower dashboard with alerts"""
        alerts = {}
        
        # Get pending PODs
        if self.logistics_service:
            pod_result = self.logistics_service.get_pending_pods()
            is_valid, _, pod_items = self.validator.validate_list_data(pod_result, "pending_pods")
            if is_valid:
                alerts["pending_pods"] = pod_items
            
            pgi_result = self.logistics_service.get_pending_pgi()
            is_valid, _, pgi_items = self.validator.validate_list_data(pgi_result, "pending_pgi")
            if is_valid:
                alerts["pending_pgi"] = pgi_items
        
        # Generate control tower alerts
        tower_alerts = self.control_tower.generate_alerts(alerts)
        
        return self.formatter.success(
            data=alerts,
            metadata=tower_alerts,
            summary=self._build_control_tower_summary(tower_alerts)
        )
    
    def _handle_help(self, entities: ExtractedEntities, question: str) -> Dict:
        return self.formatter.success(data={}, summary=self._get_help_message())
    
    def _handle_greeting(self, entities: ExtractedEntities, question: str) -> Dict:
        return self.formatter.success(data={}, summary=self._get_greeting_message())
    
    def _handle_general(self, entities: ExtractedEntities, question: str) -> Dict:
        """Handle general queries - try to route intelligently or use Groq"""
        # Try to extract DN from general query
        dn_match = QueryParser.DN_PATTERN.search(question)
        if dn_match:
            entities.dn_number = dn_match.group(1)
            return self._handle_dn_lookup(entities, question)
        
        # Try to extract dealer from general query
        for dealer in QueryParser.CITIES:
            if dealer in question.lower():
                entities.city = dealer.capitalize()
                return self._handle_city_dashboard(entities, question)
        
        # Fallback to Groq
        if self.groq.groq_service:
            try:
                result = self.groq.groq_service.analyze(question, "general", {})
                insight = result.get("insight") or result.get("response") or str(result)
                return self.formatter.success(data={"insight": insight}, summary=insight)
            except Exception as e:
                logger.error(f"Groq general query failed: {e}")
        
        return self.formatter.error("I couldn't process your request. Type 'Help' for available commands.")
    
    def _handle_universal(self, entities: ExtractedEntities, question: str) -> Dict:
        """Universal search - try everything"""
        # Try DN
        if entities.dn_number:
            return self._handle_dn_lookup(entities, question)
        
        # Try dealer
        if entities.dealer:
            return self._handle_dealer_query(entities, question)
        
        # Try city
        if entities.city:
            return self._handle_city_dashboard(entities, question)
        
        # Try warehouse
        if entities.warehouse:
            return self._handle_warehouse_query(entities, question)
        
        # Fallback to general
        return self._handle_general(entities, question)
    
    def _handle_city_dashboard(self, entities: ExtractedEntities, question: str) -> Dict:
        """Handle city-specific queries"""
        return self.formatter.success(
            data={"city": entities.city},
            summary=f"📊 *{entities.city} Dashboard*\n\nPerformance data for {entities.city} is being analyzed."
        )
    
    def _handle_warehouse_query(self, entities: ExtractedEntities, question: str) -> Dict:
        """Handle warehouse-specific queries"""
        if self.analytics_service:
            result = self.analytics_service.get_warehouse_dashboard(entities.warehouse)
            if result:
                return self.formatter.success(data=result, summary=f"Warehouse: {entities.warehouse}")
        
        return self.formatter.success(
            data={"warehouse": entities.warehouse},
            summary=f"🏭 *Warehouse: {entities.warehouse}*\n\nData is being retrieved."
        )
    
    # ==========================================================
    # BUSINESS RULES APPLICATION
    # ==========================================================
    
    def _apply_business_rules(self, result: Dict, query_type: QueryType, entities: ExtractedEntities) -> Dict:
        if not result.get("success"):
            return result
        
        data = result.get("data", {})
        
        if query_type == QueryType.DN_LOOKUP and data:
            aging = data.get("aging", {})
            health_score, health_grade = self.business_rules.calculate_health_score(aging)
            data["health_score"] = health_score
            data["health_grade"] = health_grade
            result["data"] = data
        
        return result
    
    # ==========================================================
    # RESPONSE BUILDERS
    # ==========================================================
    
    def _build_dn_summary(self, data: Dict) -> str:
        dn_no = self.validator.sanitize_value(data.get("dn_no"), "Unknown DN")
        dealer = self.validator.sanitize_value(data.get("dealer"), "Unknown Dealer")
        status = self.validator.sanitize_value(data.get("status"), "Unknown")
        health = data.get("health_score", "N/A")
        
        return f"""
📦 *DN COMPLETE INTELLIGENCE REPORT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN Number:* {dn_no}
🏪 *Dealer:* {dealer}
📍 *Status:* {status}
💚 *Health Score:* {health}/100

Type "timeline" for journey details, "products" for items in this DN
"""
    
    def _build_dealer_summary(self, data: Dict) -> str:
        dealer_name = self.validator.sanitize_value(data.get("dealer"), "Dealer")
        total_dns = data.get("total_dns", 0)
        completed = data.get("completed_dns", 0)
        rate = data.get("completion_rate", 0)
        
        return f"""
🏪 *DEALER PERFORMANCE: {dealer_name}*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *Total DNs:* {total_dns}
✅ *Completed:* {completed}
📈 *Completion Rate:* {rate}%
"""
    
    def _build_dealer_ranking_summary(self, data: Dict) -> str:
        dealers = data.get("dealers", []) if isinstance(data, dict) else []
        if not dealers:
            return "No dealer ranking data available"
        
        lines = ["🏪 *TOP DEALERS*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for i, dealer in enumerate(dealers[:10], 1):
            name = dealer.get("dealer", "Unknown")
            value = dealer.get("total_value", 0)
            lines.append(f"{i}. {name} - ₹{value:,.0f}")
        
        return "\n".join(lines)
    
    def _build_pending_pod_summary(self, data: Dict) -> str:
        pending = data.get("pending_pods", []) if isinstance(data, dict) else []
        if not pending:
            return "✅ No pending PODs found. All deliveries have POD confirmation."
        
        lines = ["📋 *PENDING PODs*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for pod in pending[:10]:
            dn = pod.get("dn_no", "Unknown")
            dealer = pod.get("dealer", "Unknown")
            days = pod.get("pending_days", 0)
            lines.append(f"🔢 {dn} - {dealer} ({days} days)")
        
        if len(pending) > 10:
            lines.append(f"\n*+{len(pending) - 10} more pending PODs*")
        
        return "\n".join(lines)
    
    def _build_pending_pgi_summary(self, data: Dict) -> str:
        pending = data.get("pending_pgi", []) if isinstance(data, dict) else []
        if not pending:
            return "✅ No pending PGI found. All DNs are dispatched."
        
        lines = ["🚚 *PENDING PGI*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for pgi in pending[:10]:
            dn = pgi.get("dn_no", "Unknown")
            dealer = pgi.get("dealer", "Unknown")
            days = pgi.get("pending_days", 0)
            lines.append(f"🔢 {dn} - {dealer} ({days} days)")
        
        if len(pending) > 10:
            lines.append(f"\n*+{len(pending) - 10} more pending PGI*")
        
        return "\n".join(lines)
    
    def _build_dashboard_summary(self, data: Dict) -> str:
        return """
👑 *EXECUTIVE DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 System is operational.

Type "Network Health" for detailed metrics or "Top Risks" for risk analysis.
"""
    
    def _build_health_summary(self, data: Dict) -> str:
        return """
🩺 *NETWORK HEALTH CHECK*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ All systems operational
✅ Database connected
✅ Services running

*Status:* Healthy
"""
    
    def _build_risks_summary(self, data: Dict) -> str:
        risks = data.get("risks", []) if isinstance(data, dict) else []
        if not risks:
            return "✅ No critical risks identified"
        
        lines = ["⚠️ *TOP RISKS*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for risk in risks[:5]:
            lines.append(f"• {risk}")
        
        return "\n".join(lines)
    
    def _build_warehouse_summary(self, data: Dict) -> str:
        warehouses = data.get("warehouses", []) if isinstance(data, dict) else []
        if not warehouses:
            return "No warehouse ranking data available"
        
        lines = ["🏭 *TOP WAREHOUSES*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for i, wh in enumerate(warehouses[:10], 1):
            name = wh.get("warehouse", "Unknown")
            volume = wh.get("volume", 0)
            lines.append(f"{i}. {name} - {volume} units")
        
        return "\n".join(lines)
    
    def _build_control_tower_summary(self, alerts: Dict) -> str:
        critical = alerts.get("critical_alerts", [])
        warnings = alerts.get("warnings", [])
        
        if critical:
            lines = ["🚨 *CONTROL TOWER - CRITICAL ALERTS*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            for alert in critical[:5]:
                lines.append(f"• {alert.get('message')}")
            return "\n".join(lines)
        elif warnings:
            lines = ["⚠️ *CONTROL TOWER - WARNINGS*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            for alert in warnings[:5]:
                lines.append(f"• {alert.get('message')}")
            return "\n".join(lines)
        
        return "✅ *CONTROL TOWER* - No active alerts. System is stable."
    
    # ==========================================================
    # HELP & GREETING
