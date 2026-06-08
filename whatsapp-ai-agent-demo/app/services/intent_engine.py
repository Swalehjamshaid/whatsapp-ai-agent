# ==========================================================
# FILE: app/services/intent_engine.py
# ==========================================================

from enum import Enum
from typing import Dict, Any, List, Optional, Tuple
import re


class IntentType(str, Enum):
    """All supported intents for the logistics platform"""
    
    # DN Intents
    DN_LOOKUP = "dn_lookup"
    DN_TIMELINE = "dn_timeline"
    DN_PRODUCTS = "dn_products"
    
    # Dealer Intents
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_RANKING = "dealer_ranking"
    DEALER_RISK = "dealer_risk"
    DEALER_SELF_SERVICE = "dealer_self_service"
    
    # Warehouse Intents
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    WAREHOUSE_RANKING = "warehouse_ranking"
    
    # City Intents
    CITY_DASHBOARD = "city_dashboard"
    CITY_RANKING = "city_ranking"
    
    # Product Intents
    PRODUCT_DASHBOARD = "product_dashboard"
    PRODUCT_RANKING = "product_ranking"
    PRODUCT_FILL_RATE = "product_fill_rate"
    FAST_MOVING = "fast_moving"
    SLOW_MOVING = "slow_moving"
    DEAD_STOCK = "dead_stock"
    
    # Division Intents
    DIVISION_DASHBOARD = "division_dashboard"
    DIVISION_RANKING = "division_ranking"
    
    # Manager Intents
    MANAGER_DASHBOARD = "manager_dashboard"
    MANAGER_RANKING = "manager_ranking"
    
    # POD/PGI Intents
    POD_ANALYSIS = "pod_analysis"
    POD_PENDING = "pod_pending"
    PGI_ANALYSIS = "pgi_analysis"
    PGI_PENDING = "pgi_pending"
    
    # Executive Intents
    EXECUTIVE_KPI = "executive_kpi"
    CEO_BRIEFING = "ceo_briefing"
    NETWORK_HEALTH = "network_health"
    TOP_RISKS = "top_risks"
    
    # Analytics Intents
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    TREND_ANALYSIS = "trend_analysis"
    PREDICTIVE_ANALYSIS = "predictive_analysis"
    
    # Revenue Intents
    REVENUE_ANALYSIS = "revenue_analysis"
    REVENUE_AT_RISK = "revenue_at_risk"
    
    # Recommendation Intents
    RECOMMENDATION = "recommendation"
    DEALER_FOLLOWUP = "dealer_followup"
    CRITICAL_DELAY_ACTION = "critical_delay_action"
    
    # Forecasting Intents
    FORECAST = "forecast"
    SALES_FORECAST = "sales_forecast"
    POD_FORECAST = "pod_forecast"
    
    # Control Tower Intents
    CONTROL_TOWER = "control_tower"
    CRITICAL_DNS = "critical_dns"
    HIGH_RISK_DNS = "high_risk_dns"
    CRITICAL_PODS = "critical_pods"
    
    # General
    HELP = "help"
    GENERAL_QUERY = "general_query"


class IntentEngine:
    """
    Intent detection engine.
    Maps natural language to structured intents.
    """
    
    # Intent patterns with priority
    INTENT_PATTERNS = {
        # DN Intents (Highest Priority)
        IntentType.DN_LOOKUP: [
            r'\bDN\s*(\d{6,15})\b',
            r'\bDelivery\s*Note\s*(\d{6,15})\b',
            r'\b(?:track|status of|check)\s+(\d{6,15})\b',
        ],
        IntentType.DN_TIMELINE: [
            r'timeline\s+(?:of\s+)?(\d{6,15})',
            r'journey\s+(?:of\s+)?(\d{6,15})',
            r'history\s+(?:of\s+)?(\d{6,15})',
        ],
        IntentType.DN_PRODUCTS: [
            r'products?\s+(?:in|of|for)\s+(\d{6,15})',
            r'items?\s+(?:in|of|for)\s+(\d{6,15})',
            r'what\s+products?\s+(?:in|for)\s+(\d{6,15})',
        ],
        
        # Executive Intents
        IntentType.EXECUTIVE_KPI: [
            r'executive\s+(?:summary|dashboard|kpi)',
            r'management\s+summary',
            r'dashboard\s+(?:only|please)',
        ],
        IntentType.CEO_BRIEFING: [
            r'ceo\s+(?:briefing|dashboard|report)',
            r'board\s+(?:briefing|report)',
            r'leadership\s+summary',
        ],
        IntentType.NETWORK_HEALTH: [
            r'network\s+health',
            r'system\s+health',
            r'overall\s+health',
        ],
        IntentType.TOP_RISKS: [
            r'top\s+risks?',
            r'biggest\s+risks?',
            r'critical\s+risks?',
            r'high\s+risk\s+dealers?',
        ],
        
        # Analytics Intents
        IntentType.ROOT_CAUSE_ANALYSIS: [
            r'why\s+(?:are|is|do|did)',
            r'root\s+cause',
            r'reason\s+for',
            r'what\s+caused',
            r'why\s+delays?\?',
        ],
        IntentType.TREND_ANALYSIS: [
            r'trend',
            r'over\s+time',
            r'pattern',
            r'compare\s+(?:with|to)',
            r'vs\s+last',
        ],
        IntentType.PREDICTIVE_ANALYSIS: [
            r'predict',
            r'forecast',
            r'will\s+(?:miss|be|hit)',
            r'likely\s+to',
            r'estimate',
        ],
        
        # Warehouse Intents
        IntentType.WAREHOUSE_DASHBOARD: [
            r'warehouse\s+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance|report)|$)',
        ],
        IntentType.WAREHOUSE_RANKING: [
            r'warehouse\s+ranking',
            r'top\s+warehouses?',
            r'best\s+warehouses?',
        ],
        
        # City Intents
        IntentType.CITY_DASHBOARD: [
            r'city\s+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance|report)|$)',
        ],
        IntentType.CITY_RANKING: [
            r'city\s+ranking',
            r'top\s+cities?',
            r'best\s+cities?',
        ],
        
        # Product Intents
        IntentType.PRODUCT_DASHBOARD: [
            r'product\s+([A-Z0-9\-]+)',
            r'([A-Z]{2,3}-[0-9A-Z]+)',
        ],
        IntentType.PRODUCT_RANKING: [
            r'product\s+ranking',
            r'top\s+products?',
            r'best\s+(?:selling|moving)\s+products?',
        ],
        IntentType.FAST_MOVING: [
            r'fast\s+moving',
            r'fastest\s+moving',
            r'high\s+velocity',
        ],
        IntentType.SLOW_MOVING: [
            r'slow\s+moving',
            r'slowest\s+moving',
            r'low\s+velocity',
        ],
        IntentType.DEAD_STOCK: [
            r'dead\s+stock',
            r'non\s+moving',
            r'obsolete',
        ],
        
        # Dealer Intents
        IntentType.DEALER_DASHBOARD: [
            r'dealer\s+([A-Za-z0-9\s&]+?)(?:\s+(?:dashboard|performance|report)|$)',
        ],
        IntentType.DEALER_RANKING: [
            r'dealer\s+ranking',
            r'top\s+dealers?',
            r'best\s+dealers?',
        ],
        IntentType.DEALER_RISK: [
            r'high\s+risk\s+dealers?',
            r'risky\s+dealers?',
            r'dealer\s+risk',
        ],
        IntentType.DEALER_SELF_SERVICE: [
            r'my\s+(?:dns?|sales?|pod|orders)',
            r'my\s+performance',
        ],
        
        # POD/PGI Intents
        IntentType.POD_PENDING: [
            r'pending\s+pod',
            r'pod\s+pending',
            r'missing\s+pod',
        ],
        IntentType.POD_ANALYSIS: [
            r'pod\s+analysis',
            r'pod\s+performance',
        ],
        IntentType.PGI_PENDING: [
            r'pending\s+pgi',
            r'pgi\s+pending',
            r'pending\s+dispatch',
        ],
        
        # Revenue Intents
        IntentType.REVENUE_ANALYSIS: [
            r'revenue\s+analysis',
            r'revenue\s+report',
            r'sales\s+analysis',
        ],
        IntentType.REVENUE_AT_RISK: [
            r'revenue\s+at\s+risk',
            r'at\s+risk\s+revenue',
        ],
        
        # Recommendation Intents
        IntentType.RECOMMENDATION: [
            r'recommendation',
            r'suggestion',
            r'action\s+items?',
            r'what\s+should\s+i\s+do',
        ],
        IntentType.DEALER_FOLLOWUP: [
            r'which\s+dealer\s+needs?\s+follow[-\s]?up',
            r'follow[-\s]?up\s+with\s+dealers?',
        ],
        
        # Forecasting Intents
        IntentType.SALES_FORECAST: [
            r'forecast\s+sales',
            r'sales\s+forecast',
            r'predict\s+sales',
        ],
        IntentType.POD_FORECAST: [
            r'forecast\s+pod',
            r'pod\s+forecast',
        ],
        
        # Control Tower Intents
        IntentType.CONTROL_TOWER: [
            r'control\s+tower',
            r'critical\s+alerts?',
            r'urgent\s+issues?',
        ],
        IntentType.CRITICAL_DNS: [
            r'critical\s+dns?',
            r'urgent\s+dns?',
        ],
        
        # Help
        IntentType.HELP: [
            r'^help$',
            r'^menu$',
            r'^commands$',
            r'what\s+can\s+you\s+do',
        ],
    }
    
    # Default confidence scores
    DEFAULT_CONFIDENCE = 0.85
    PATTERN_CONFIDENCE = 0.95
    CONTEXT_CONFIDENCE = 0.70
    
    def __init__(self):
        # Compile all regex patterns
        self.compiled_patterns = {}
        for intent, patterns in self.INTENT_PATTERNS.items():
            self.compiled_patterns[intent] = [
                re.compile(pattern, re.IGNORECASE) for pattern in patterns
            ]
    
    def detect_intent(
        self,
        question: str,
        entities: Dict,
        context: Dict
    ) -> Tuple[IntentType, Optional[str], float]:
        """
        Detect intent from question, entities, and context.
        
        Returns:
            Tuple of (intent, entity_value, confidence)
        """
        question_lower = question.lower().strip()
        
        # Priority 1: DN Number present
        from app.services.entity_extractor import EntityType
        if EntityType.DN_NUMBER in entities:
            dn = entities[EntityType.DN_NUMBER].value
            
            # Check for timeline/products first
            if any(p in question_lower for p in ['timeline', 'journey', 'history']):
                return IntentType.DN_TIMELINE, dn, self.PATTERN_CONFIDENCE
            if any(p in question_lower for p in ['product', 'items', 'contains']):
                return IntentType.DN_PRODUCTS, dn, self.PATTERN_CONFIDENCE
            
            # Default DN lookup
            return IntentType.DN_LOOKUP, dn, self.PATTERN_CONFIDENCE
        
        # Priority 2: Dealer self-service (phone to dealer mapping)
        if context.get('dealer_name') and any(p in question_lower for p in ['my', 'my dns', 'my sales']):
            return IntentType.DEALER_SELF_SERVICE, context.get('dealer_name'), self.CONTEXT_CONFIDENCE
        
        # Priority 3: Pattern matching
        for intent, patterns in self.compiled_patterns.items():
            for pattern in patterns:
                match = pattern.search(question)
                if match:
                    # Extract entity if present
                    entity_value = None
                    if match.groups():
                        entity_value = match.group(1).strip()
                    return intent, entity_value, self.PATTERN_CONFIDENCE
        
        # Priority 4: Context-based follow-up
        if context.get('last_intent') and self._is_follow_up(question_lower):
            last_intent = IntentType(context.get('last_intent'))
            last_entity = context.get('last_entity')
            if last_entity:
                return last_intent, last_entity, self.CONTEXT_CONFIDENCE
        
        # Priority 5: General query - will go to AI
        return IntentType.GENERAL_QUERY, None, self.DEFAULT_CONFIDENCE
    
    def _is_follow_up(self, question_lower: str) -> bool:
        """Check if question is a follow-up to previous context"""
        follow_up_indicators = [
            'it', 'this', 'that', 'the dn', 'the dealer', 
            'the product', 'the warehouse', 'the city',
            'tell me more', 'elaborate', 'details'
        ]
        return any(indicator in question_lower for indicator in follow_up_indicators)
