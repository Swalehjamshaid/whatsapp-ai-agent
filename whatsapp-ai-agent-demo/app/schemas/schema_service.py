# ==========================================================
# FILE: app/schemas/schema_service.py
# PURPOSE: Schema Metadata Service
# ==========================================================

from typing import Dict, List, Optional, Tuple
from threading import Lock


class SchemaService:
    """
    Central metadata repository used by AIQueryService.
    """

    def __init__(self):

        # ==========================================================
        # WAREHOUSES
        # ==========================================================

        self.warehouses: Dict[str, str] = {
            "lhr": "Lahore Warehouse",
            "khi": "Karachi Warehouse",
            "isb": "Islamabad Warehouse",
        }

        # ==========================================================
        # CITIES
        # ==========================================================

        self.cities: Dict[str, str] = {
            "lahore": "Lahore",
            "karachi": "Karachi",
            "islamabad": "Islamabad",
            "lhr": "Lahore",
            "khi": "Karachi",
            "isb": "Islamabad",
        }

        # ==========================================================
        # DEALERS
        # ==========================================================

        self.dealers: Dict[str, str] = {
            "nce": "NCE",
        }

        # ==========================================================
        # LOGISTICS KEYWORDS
        # ==========================================================

        self.logistics_keywords = {
            "pending",
            "delivered",
            "warehouse",
            "dealer",
            "city",
            "revenue",
            "units",
            "pgi",
            "pod",
            "aging",
            "top",
            "bottom",
        }

        # ==========================================================
        # INTENTS
        # ==========================================================

        self.intents = {
            "pending_pgi": [
                "pending pgi",
                "open pgi",
            ],
            "pending_pod": [
                "pending pod",
                "open pod",
            ],
            "pgi_aging": [
                "pgi aging",
                "aging pgi",
            ],
            "pod_aging": [
                "pod aging",
                "aging pod",
            ],
            "top_dealers": [
                "top dealer",
                "best dealer",
            ],
            "bottom_dealers": [
                "bottom dealer",
                "worst dealer",
            ],
            "executive_insight": [
                "executive insight",
            ],
            "root_cause": [
                "root cause",
                "why",
            ],
        }

        # ==========================================================
        # METRICS
        # ==========================================================

        self.metrics = {
            "revenue": [
                "revenue",
                "sales",
                "amount",
            ],
            "units": [
                "units",
                "quantity",
                "qty",
            ],
        }

    # ==========================================================
    # INTENT DETECTION
    # ==========================================================

    def detect_intent(self, text: str) -> Tuple[Optional[str], float]:

        if not text:
            return None, 0.0

        text = text.lower()

        for intent, keywords in self.intents.items():
            for keyword in keywords:
                if keyword in text:
                    return intent, 0.90

        return None, 0.0

    # ==========================================================
    # METRIC DETECTION
    # ==========================================================

    def detect_metric(self, text: str) -> Optional[str]:

        if not text:
            return None

        text = text.lower()

        for metric, keywords in self.metrics.items():
            for keyword in keywords:
                if keyword in text:
                    return metric

        return None

    # ==========================================================
    # DEALER RESOLUTION
    # ==========================================================

    def resolve_dealer(self, text: str) -> Optional[str]:

        if not text:
            return None

        text = text.lower().strip()

        for alias, dealer in self.dealers.items():
            if alias == text or dealer.lower() == text:
                return dealer

        return None

    # ==========================================================
    # WAREHOUSE RESOLUTION
    # ==========================================================

    def resolve_warehouse(self, text: str) -> Optional[str]:

        if not text:
            return None

        text = text.lower().strip()

        return self.warehouses.get(text)

    # ==========================================================
    # CITY RESOLUTION
    # ==========================================================

    def resolve_city(self, text: str) -> Optional[str]:

        if not text:
            return None

        text = text.lower().strip()

        return self.cities.get(text)

    # ==========================================================
    # LOGISTICS KEYWORD CHECK
    # ==========================================================

    def is_logistics_keyword(self, text: str) -> bool:

        if not text:
            return False

        text = text.lower()

        return any(keyword in text for keyword in self.logistics_keywords)

    # ==========================================================
    # METADATA VALIDATION
    # ==========================================================

    def validate_metadata(self):

        return {
            "counts": {
                "dealers": len(self.dealers),
                "warehouses": len(self.warehouses),
                "cities": len(self.cities),
                "intents": len(self.intents),
                "metrics": len(self.metrics),
            }
        }


# ==========================================================
# SINGLETON
# ==========================================================

_schema_service = None
_schema_lock = Lock()


def get_schema_service() -> SchemaService:

    global _schema_service

    if _schema_service is None:
        with _schema_lock:
            if _schema_service is None:
                _schema_service = SchemaService()

    return _schema_service


# ==========================================================
# REPORT
# ==========================================================

def generate_metadata_report():

    service = get_schema_service()

    return service.validate_metadata()
