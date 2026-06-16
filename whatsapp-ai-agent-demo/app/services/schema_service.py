# ==========================================================
# FILE: app/schemas/schema_service.py (v1.1 - METADATA LAYER)
# ==========================================================
# PURPOSE: Business Metadata Engine - Single Source of Truth
# CHANGES: Thread-safe singleton, startup validation, diagnostics
# ==========================================================

from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass, field
import threading
import logging


# ==========================================================
# LOGGING SETUP
# ==========================================================

logger = logging.getLogger(__name__)


# ==========================================================
# MASTER DATA
# ==========================================================

DEALER_MASTER: Dict[str, str] = {
    "nce": "New China Electronics",
    "new china": "New China Electronics",
    "china electronics": "New China Electronics",
    "ag": "Abdullah Group",
    "abdullah group": "Abdullah Group",
    "mg": "Mian Group",
    "mian group": "Mian Group",
    "sg": "Saeed Group",
    "saeed group": "Saeed Group",
    "ali traders": "Ali Traders",
    "alitraders": "Ali Traders",
    "khan electronics": "Khan Electronics",
    "khan": "Khan Electronics",
    "raza traders": "Raza Traders",
    "raza": "Raza Traders",
    "hassan traders": "Hassan Traders",
    "hassan": "Hassan Traders",
    "usman enterprises": "Usman Enterprises",
    "usman": "Usman Enterprises",
}

WAREHOUSE_MASTER: Dict[str, str] = {
    "lhr": "Lahore",
    "lahore": "Lahore",
    "rwp": "Rawalpindi",
    "rawalpindi": "Rawalpindi",
    "isb": "Islamabad",
    "islamabad": "Islamabad",
    "khi": "Karachi",
    "karachi": "Karachi",
    "mux": "Multan",
    "multan": "Multan",
    "fsd": "Faisalabad",
    "faisalabad": "Faisalabad",
    "pwr": "Peshawar",
    "peshawar": "Peshawar",
    "qta": "Quetta",
    "quetta": "Quetta",
}

CITY_MASTER: Dict[str, str] = {
    "lhr": "Lahore",
    "lahore": "Lahore",
    "rwp": "Rawalpindi",
    "rawalpindi": "Rawalpindi",
    "isb": "Islamabad",
    "islamabad": "Islamabad",
    "khi": "Karachi",
    "karachi": "Karachi",
    "mux": "Multan",
    "multan": "Multan",
    "fsd": "Faisalabad",
    "faisalabad": "Faisalabad",
}

# ==========================================================
# INTENT KEYWORDS
# ==========================================================

INTENT_KEYWORDS: Dict[str, List[str]] = {
    "help": ["help", "menu", "commands", "what can you do"],
    "dn_lookup": ["dn", "delivery note", "track dn", "dn details"],
    "dealer_dashboard": ["dealer", "show dealer", "dealer dashboard"],
    "dealer_revenue": ["revenue", "sales", "amount", "total revenue"],
    "dealer_units": ["units", "quantity", "qty", "total units"],
    "dealer_performance": ["performance", "kpi", "dealer performance"],
    "dealer_aging": ["aging", "delay", "pending", "oldest"],
    "warehouse_dashboard": ["warehouse", "show warehouse", "warehouse summary"],
    "warehouse_performance": ["warehouse performance", "warehouse kpi"],
    "pending_pgi": ["pending pgi", "pgi pending", "pgi not done"],
    "pending_pod": ["pending pod", "pod pending", "pod not done"],
    "pgi_aging": ["pgi aging", "pgi delay"],
    "pod_aging": ["pod aging", "pod delay"],
    "top_dealers": ["top dealer", "best dealer", "highest dealer"],
    "bottom_dealers": ["bottom dealer", "worst dealer", "lowest dealer"],
    "top_warehouses": ["top warehouse", "best warehouse"],
    "executive_insight": ["key issue", "biggest problem", "bottleneck", "executive insight"],
    "control_tower": ["critical", "alert", "urgent", "control tower"],
    "root_cause": ["why", "root cause", "reason", "cause"],
    "trend": ["trend", "month over month", "trends"],
    "comparison": ["compare", "vs", "versus"],
    "general_ai": ["hello", "hi", "hey", "how are you"],
}

# ==========================================================
# METRIC KEYWORDS
# ==========================================================

METRIC_KEYWORDS: Dict[str, List[str]] = {
    "revenue": ["revenue", "sales", "amount", "total revenue"],
    "units": ["units", "quantity", "qty", "total units"],
    "dn_count": ["dns", "delivery notes", "orders", "total dns"],
    "pending_pod": ["pending pod", "pod pending", "pod not done"],
    "pending_delivery": ["pending delivery", "delivery pending", "pending pgi"],
    "delivery_aging": ["delivery aging", "pgi aging", "delivery delay"],
    "pod_aging": ["pod aging", "pod delay", "pod latency"],
    "pod_rate": ["pod rate", "pod percentage", "pod completion"],
    "pgi_rate": ["pgi rate", "pgi percentage", "pgi completion"],
    "delivery_rate": ["delivery rate", "delivery percentage", "delivery completion"],
}

# ==========================================================
# LOGISTICS KEYWORDS (Reject List)
# ==========================================================

LOGISTICS_KEYWORDS: Set[str] = {
    'pending', 'pgi', 'pod', 'aging', 'delivery', 'revenue', 'units',
    'performance', 'critical', 'alert', 'control', 'tower', 'top',
    'help', 'menu', 'status', 'what', 'how', 'why', 'when', 'where',
    'who', 'which', 'can', 'could', 'would', 'should', 'is', 'are',
    'show', 'display', 'get', 'tell', 'warehouse', 'summary', 'report',
    'kpi', 'dashboard', 'insight', 'issue', 'problem', 'bottleneck',
    'transit', 'delivered', 'rate', 'completion', 'dn', 'order'
}

# ==========================================================
# BUSINESS RULES
# ==========================================================

BUSINESS_RULES: Dict[str, Any] = {
    "delivery_aging": {
        "rule": "IF PGI EXISTS THEN delivery_aging = PGI_Date - DN_Creation_Date ELSE delivery_aging = Today - DN_Creation_Date",
        "thresholds": {"critical": 30, "high": 15, "medium": 7, "low": 3}
    },
    "pod_aging": {
        "rule": "IF POD EXISTS THEN pod_aging = POD_Date - PGI_Date ELSE pod_aging = Today - PGI_Date",
        "thresholds": {"critical": 30, "high": 15, "medium": 7, "low": 3}
    },
    "sla": {
        "delivery_aging_target": 7,
        "pod_aging_target": 7,
        "delivery_rate_target": 90,
        "pod_rate_target": 90
    }
}

# ==========================================================
# STATUS DEFINITIONS
# ==========================================================

STATUS_DEFINITIONS: Dict[str, Dict[str, str]] = {
    "dn_status": {
        "delivered": "✅ Delivered - POD Received",
        "in_transit": "🚚 In Transit - PGI Done, POD Pending",
        "pending_pgi": "⏳ Pending PGI - Not Yet Dispatched",
        "unknown": "❓ Status Unknown",
    },
    "risk_status": {
        "critical": "🔴 CRITICAL - Immediate Attention Required",
        "high": "🟠 HIGH - Action Required",
        "medium": "🟡 MEDIUM - Monitor Closely",
        "low": "🟢 LOW - Normal Operations",
    }
}


# ==========================================================
# SCHEMA SERVICE CLASS
# ==========================================================

class SchemaService:
    """Business Metadata Engine - Single Source of Truth"""
    
    def __init__(self):
        self.dealers = DEALER_MASTER
        self.warehouses = WAREHOUSE_MASTER
        self.cities = CITY_MASTER
        self.intents = INTENT_KEYWORDS
        self.metrics = METRIC_KEYWORDS
        self.statuses = STATUS_DEFINITIONS
        self.rules = BUSINESS_RULES
        self.logistics_keywords = LOGISTICS_KEYWORDS
        
        # Run validation on initialization
        self._validate_metadata()
    
    def resolve_dealer(self, dealer_input: str) -> Optional[str]:
        if not dealer_input:
            return None
        input_lower = dealer_input.lower().strip()
        if input_lower in self.dealers:
            return self.dealers[input_lower]
        for alias, full_name in self.dealers.items():
            if alias in input_lower or input_lower in alias:
                return full_name
        return None
    
    def resolve_warehouse(self, warehouse_input: str) -> Optional[str]:
        if not warehouse_input:
            return None
        input_lower = warehouse_input.lower().strip()
        if input_lower in self.warehouses:
            return self.warehouses[input_lower]
        for full_name in set(self.warehouses.values()):
            if full_name.lower() == input_lower:
                return full_name
        return None
    
    def resolve_city(self, city_input: str) -> Optional[str]:
        if not city_input:
            return None
        input_lower = city_input.lower().strip()
        if input_lower in self.cities:
            return self.cities[input_lower]
        for full_name in set(self.cities.values()):
            if full_name.lower() == input_lower:
                return full_name
        return None
    
    def detect_intent(self, text: str) -> tuple[Optional[str], float]:
        text_lower = text.lower().strip()
        for intent, keywords in self.intents.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return intent, 0.9
        return None, 0.0
    
    def detect_metric(self, text: str) -> Optional[str]:
        text_lower = text.lower().strip()
        for metric, keywords in self.metrics.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return metric
        return None
    
    def is_logistics_keyword(self, text: str) -> bool:
        return text.lower().strip() in self.logistics_keywords
    
    def get_risk_status(self, score: float) -> str:
        if score < 50:
            return "critical"
        elif score < 70:
            return "high"
        elif score < 85:
            return "medium"
        return "low"
    
    def get_risk_emoji(self, status: str) -> str:
        emojis = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
        return emojis.get(status, "⚪")
    
    def get_dn_status(self, status_key: str) -> str:
        return self.statuses.get("dn_status", {}).get(status_key, "❓ Unknown")
    
    def get_rule(self, rule_name: str) -> Optional[Any]:
        return self.rules.get(rule_name)
    
    def _validate_metadata(self) -> Dict[str, Any]:
        """Validate metadata integrity and return diagnostic report."""
        report = {
            "status": "valid",
            "issues": [],
            "counts": {
                "dealers": len(self.dealers),
                "warehouses": len(self.warehouses),
                "cities": len(self.cities),
                "intents": len(self.intents),
                "metrics": len(self.metrics)
            }
        }
        
        # Check for duplicates in intent keywords
        all_intent_keywords = []
        for intent, keywords in self.intents.items():
            for keyword in keywords:
                if keyword in all_intent_keywords:
                    report["issues"].append(f"Duplicate keyword '{keyword}' in intent: {intent}")
                all_intent_keywords.append(keyword)
        
        # Check for duplicates in metric keywords
        all_metric_keywords = []
        for metric, keywords in self.metrics.items():
            for keyword in keywords:
                if keyword in all_metric_keywords:
                    report["issues"].append(f"Duplicate keyword '{keyword}' in metric: {metric}")
                all_metric_keywords.append(keyword)
        
        # Check for dealer alias conflicts
        all_dealer_aliases = []
        for alias, full_name in self.dealers.items():
            if alias in all_dealer_aliases:
                report["issues"].append(f"Duplicate dealer alias '{alias}' maps to: {full_name}")
            all_dealer_aliases.append(alias)
        
        if report["issues"]:
            report["status"] = "warning"
            logger.warning(f"Metadata validation found {len(report['issues'])} issues")
            for issue in report["issues"]:
                logger.warning(f"  - {issue}")
        else:
            logger.info("Metadata validation passed - all data consistent")
        
        return report
    
    def validate_metadata(self) -> Dict[str, Any]:
        """Public method to get metadata validation report."""
        return self._validate_metadata()
    
    def get_metadata_report(self) -> Dict[str, Any]:
        """Generate comprehensive metadata validation report."""
        report = {
            "metadata": {
                "dealers": list(self.dealers.keys()),
                "warehouses": list(self.warehouses.keys()),
                "cities": list(self.cities.keys()),
                "intents": list(self.intents.keys()),
                "metrics": list(self.metrics.keys())
            },
            "duplicates": {
                "intent_keywords": [],
                "metric_keywords": [],
                "dealer_aliases": []
            },
            "business_rules": self.rules,
            "statuses": self.statuses,
            "stats": {
                "total_dealers": len(self.dealers),
                "total_warehouses": len(self.warehouses),
                "total_cities": len(self.cities),
                "total_intents": len(self.intents),
                "total_metrics": len(self.metrics),
                "total_logistics_keywords": len(self.logistics_keywords)
            }
        }
        
        # Detect duplicate intent keywords
        seen_keywords = set()
        for intent, keywords in self.intents.items():
            for keyword in keywords:
                if keyword in seen_keywords:
                    report["duplicates"]["intent_keywords"].append({
                        "keyword": keyword,
                        "intent": intent
                    })
                seen_keywords.add(keyword)
        
        # Detect duplicate metric keywords
        seen_keywords = set()
        for metric, keywords in self.metrics.items():
            for keyword in keywords:
                if keyword in seen_keywords:
                    report["duplicates"]["metric_keywords"].append({
                        "keyword": keyword,
                        "metric": metric
                    })
                seen_keywords.add(keyword)
        
        # Detect duplicate dealer aliases
        seen_aliases = set()
        for alias in self.dealers.keys():
            if alias in seen_aliases:
                report["duplicates"]["dealer_aliases"].append({
                    "alias": alias
                })
            seen_aliases.add(alias)
        
        return report


# ==========================================================
# THREAD-SAFE SINGLETON
# ==========================================================

_schema_service = None
_schema_lock = threading.Lock()


def get_schema_service() -> SchemaService:
    """Thread-safe singleton getter for SchemaService.
    
    Returns:
        SchemaService: The singleton instance of SchemaService
        
    Note:
        This implementation uses double-checked locking for thread safety
        while maintaining backward compatibility with existing code.
    """
    global _schema_service
    
    if _schema_service is None:
        with _schema_lock:
            if _schema_service is None:
                _schema_service = SchemaService()
                logger.info("SchemaService initialized successfully")
                
                # Log metadata statistics
                report = _schema_service._validate_metadata()
                logger.info(f"Metadata loaded: {report['counts']}")
                
                if report["status"] == "warning":
                    logger.warning(f"Metadata validation warnings: {len(report['issues'])} issues found")
    
    return _schema_service


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def generate_metadata_report() -> Dict[str, Any]:
    """Generate comprehensive metadata validation report.
    
    Returns:
        Dict[str, Any]: Complete metadata validation report
        
    Example:
        >>> report = generate_metadata_report()
        >>> print(report['stats']['total_dealers'])
        25
    """
    service = get_schema_service()
    return service.get_metadata_report()


# ==========================================================
# MODULE INITIALIZATION LOGGING
# ==========================================================

# Log module loading (only when imported)
logger.debug("Schema service module loaded")
