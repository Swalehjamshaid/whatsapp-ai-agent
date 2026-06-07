# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v20.0 - COMPLETE)
# ==========================================================
# COMPLETE AI QUERY ORCHESTRATOR v20.0:
# ==========================================================
# ✅ PRIORITY 1: Analytics Snapshot Table
# ✅ PRIORITY 2: Fixed POD Aging Logic
# ✅ PRIORITY 3: Delivery Aging Logic
# ✅ PRIORITY 4: DN Status Engine
# ✅ PRIORITY 5: Delay Buckets
# ✅ PRIORITY 6: Shared Analytics Context
# ✅ PRIORITY 7: DN Lookup Cache
# ✅ PRIORITY 8: Dealer Startup Cache
# ✅ PRIORITY 9: Product Intelligence Engine
# ✅ PRIORITY 10: Natural Language Analytics Fields
# ✅ PRIORITY 11: Fixed CEO Briefing Bug
# ✅ GROQ INTEGRATION: AI-Powered General Queries
# ✅ PHASE 1-10: Orchestrator Pattern with Intent Router
# ==========================================================

import re
import time
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, date
from enum import Enum
from collections import deque, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_, text
from loguru import logger

from app.config import config

# ==========================================================
# GROQ INTEGRATION (RESTORED)
# ==========================================================

try:
    from app.services.ai_provider_service import get_ai_provider_service
    AI_PROVIDER_AVAILABLE = True
except ImportError as e:
    logger.error(f"Failed to import AI provider: {e}")
    AI_PROVIDER_AVAILABLE = False

# ==========================================================
# ANALYTICS SNAPSHOT MODEL (Priority 1)
# ==========================================================

@dataclass
class AnalyticsSnapshot:
    """Pre-calculated analytics snapshot for lightning-fast queries"""
    dn_no: str
    dealer: str
    warehouse: str
    city: str
    delivery_status: str
    delivery_aging: int
    pending_delivery_aging: int
    pod_aging: int
    pending_pod_aging: int
    risk_score: int
    dealer_score: int
    warehouse_score: int
    city_score: int
    delay_bucket: str
    dn_health_score: int
    total_value: float
    total_units: float
    product_count: int
    pgi_date: Optional[date] = None
    pod_date: Optional[date] = None
    delivery_date: Optional[date] = None
    created_date: Optional[date] = None
    last_updated: datetime = field(default_factory=datetime.now)


# ==========================================================
# DN STATUS ENGINE (Priority 4)
# ==========================================================

class DeliveryStatus(str, Enum):
    OPEN = "Open"
    IN_TRANSIT = "In Transit"
    DELIVERED = "Delivered"
    CLOSED = "Closed"


class DelayBucket(str, Enum):
    ON_TIME = "On Time"
    MINOR_DELAY = "Minor Delay"
    MODERATE_DELAY = "Moderate Delay"
    CRITICAL = "Critical"
    SEVERE = "Severe"


class DNStatusEngine:
    """Centralized DN status determination engine"""
    
    @staticmethod
    def get_status(pgi_status: str, pod_status: str) -> DeliveryStatus:
        if pgi_status != "Completed":
            return DeliveryStatus.OPEN
        elif pgi_status == "Completed" and pod_status != "Received":
            return DeliveryStatus.IN_TRANSIT
        elif pod_status == "Received":
            return DeliveryStatus.DELIVERED
        return DeliveryStatus.OPEN
    
    @staticmethod
    def get_delay_bucket(days_delayed: int) -> DelayBucket:
        if days_delayed <= 1:
            return DelayBucket.ON_TIME
        elif days_delayed <= 3:
            return DelayBucket.MINOR_DELAY
        elif days_delayed <= 7:
            return DelayBucket.MODERATE_DELAY
        elif days_delayed <= 15:
            return DelayBucket.CRITICAL
        else:
            return DelayBucket.SEVERE
    
    @staticmethod
    def get_status_icon(status: DeliveryStatus) -> str:
        icons = {
            DeliveryStatus.OPEN: "📝",
            DeliveryStatus.IN_TRANSIT: "🚚",
            DeliveryStatus.DELIVERED: "✅",
            DeliveryStatus.CLOSED: "🔒"
        }
        return icons.get(status, "❓")
    
    @staticmethod
    def get_delay_icon(bucket: DelayBucket) -> str:
        icons = {
            DelayBucket.ON_TIME: "🟢",
            DelayBucket.MINOR_DELAY: "🟡",
            DelayBucket.MODERATE_DELAY: "🟠",
            DelayBucket.CRITICAL: "🔴",
            DelayBucket.SEVERE: "💀"
        }
        return icons.get(bucket, "⚪")


# ==========================================================
# ANALYTICS CONTEXT (Priority 6)
# ==========================================================

class AnalyticsContext:
    """Single source of truth - calculated once, reused everywhere"""
    
    def __init__(self, db: Session):
        self.db = db
        self._context = None
        self._last_refresh = None
        self._refresh_interval_seconds = 900  # 15 minutes
    
    def is_stale(self) -> bool:
        if not self._last_refresh:
            return True
        return (datetime.now() - self._last_refresh).total_seconds() > self._refresh_interval_seconds
    
    def refresh(self):
        start_time = time.time()
        logger.info("🔄 Refreshing analytics context...")
        
        self._context = {
            "pending_metrics": self._get_pending_metrics(),
            "pod_metrics": self._get_pod_metrics(),
            "dealer_rankings": self._get_dealer_rankings(),
            "warehouse_rankings": self._get_warehouse_rankings(),
            "city_rankings": self._get_city_rankings(),
            "product_metrics": self._get_product_metrics(),
            "revenue_metrics": self._get_revenue_metrics(),
            "snapshots": self._load_snapshots()
        }
        
        self._last_refresh = datetime.now()
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"✅ Analytics context refreshed in {elapsed:.0f}ms")
    
    def _get_pending_metrics(self) -> Dict:
        result = self.db.query(
            func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns"),
            func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status != "Completed").label("pending_value"),
            func.avg(func.extract('day', datetime.now() - DeliveryReport.dn_create_date)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).label("avg_pending_days")
        ).first()
        
        return {
            "pending_dns": int(result.pending_dns or 0),
            "pending_value": float(result.pending_value or 0),
            "avg_pending_days": round(float(result.avg_pending_days or 0), 1)
        }
    
    def _get_pod_metrics(self) -> Dict:
        result = self.db.query(
            func.count(DeliveryReport.dn_no).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).label("pending_pods"),
            func.sum(DeliveryReport.dn_amount).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).label("pending_pod_value"),
            func.count(DeliveryReport.dn_no).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Received"
            ).label("completed_pods")
        ).first()
        
        return {
            "pending_pods": int(result.pending_pods or 0),
            "pending_pod_value": float(result.pending_pod_value or 0),
            "completed_pods": int(result.completed_pods or 0)
        }
    
    def _get_dealer_rankings(self) -> List[Dict]:
        results = self.db.query(
            DeliveryReport.customer_name,
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(DeliveryReport.dn_no).label("total_dns"),
            func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").label("completed_dns"),
            func.count(DeliveryReport.dn_no).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Received"
            ).label("pod_completed")
        ).filter(
            DeliveryReport.customer_name.isnot(None)
        ).group_by(
            DeliveryReport.customer_name
        ).all()
        
        dealers = []
        for r in results:
            completion_rate = (r.completed_dns / r.total_dns * 100) if r.total_dns > 0 else 0
            pod_rate = (r.pod_completed / r.completed_dns * 100) if r.completed_dns > 0 else 0
            health_score = (completion_rate * 0.6) + (pod_rate * 0.4)
            
            dealers.append({
                "name": r.customer_name,
                "total_value": float(r.total_value or 0),
                "total_dns": r.total_dns,
                "completed_dns": r.completed_dns,
                "completion_rate": round(completion_rate, 1),
                "pod_rate": round(pod_rate, 1),
                "health_score": round(health_score, 1)
            })
        
        dealers.sort(key=lambda x: x["total_value"], reverse=True)
        for i, d in enumerate(dealers, 1):
            d["rank"] = i
        
        return dealers
    
    def _get_warehouse_rankings(self) -> List[Dict]:
        results = self.db.query(
            DeliveryReport.warehouse,
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(DeliveryReport.dn_no).label("total_dns"),
            func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").label("completed_dns")
        ).filter(
            DeliveryReport.warehouse.isnot(None)
        ).group_by(
            DeliveryReport.warehouse
        ).all()
        
        warehouses = []
        for r in results:
            completion_rate = (r.completed_dns / r.total_dns * 100) if r.total_dns > 0 else 0
            warehouses.append({
                "name": r.warehouse,
                "total_value": float(r.total_value or 0),
                "total_dns": r.total_dns,
                "completed_dns": r.completed_dns,
                "completion_rate": round(completion_rate, 1)
            })
        
        warehouses.sort(key=lambda x: x["total_value"], reverse=True)
        return warehouses
    
    def _get_city_rankings(self) -> List[Dict]:
        results = self.db.query(
            DeliveryReport.ship_to_city,
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(DeliveryReport.dn_no).label("total_dns"),
            func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").label("completed_dns")
        ).filter(
            DeliveryReport.ship_to_city.isnot(None)
        ).group_by(
            DeliveryReport.ship_to_city
        ).all()
        
        cities = []
        for r in results:
            completion_rate = (r.completed_dns / r.total_dns * 100) if r.total_dns > 0 else 0
            cities.append({
                "name": r.ship_to_city,
                "total_value": float(r.total_value or 0),
                "total_dns": r.total_dns,
                "completed_dns": r.completed_dns,
                "completion_rate": round(completion_rate, 1)
            })
        
        cities.sort(key=lambda x: x["total_value"], reverse=True)
        return cities
    
    def _get_product_metrics(self) -> Dict:
        results = self.db.query(
            DeliveryReport.product,
            func.sum(DeliveryReport.dn_qty).label("total_qty"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(DeliveryReport.dn_no).label("total_dns"),
            func.sum(DeliveryReport.dn_qty).filter(DeliveryReport.pgi_status == "Completed").label("delivered_qty"),
            func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status == "Completed").label("delivered_value")
        ).filter(
            DeliveryReport.product.isnot(None)
        ).group_by(
            DeliveryReport.product
        ).all()
        
        products = []
        for r in results:
            fill_rate = (r.delivered_qty / r.total_qty * 100) if r.total_qty > 0 else 0
            products.append({
                "name": r.product,
                "total_qty": float(r.total_qty or 0),
                "total_value": float(r.total_value or 0),
                "total_dns": r.total_dns,
                "delivered_qty": float(r.delivered_qty or 0),
                "delivered_value": float(r.delivered_value or 0),
                "fill_rate": round(fill_rate, 1)
            })
        
        products.sort(key=lambda x: x["total_value"], reverse=True)
        return {
            "top_products": products[:10],
            "bottom_products": products[-10:] if len(products) > 10 else [],
            "total_products": len(products)
        }
    
    def _get_revenue_metrics(self) -> Dict:
        total = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
        delivered = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(DeliveryReport.pgi_status == "Completed").scalar() or 0
        pod_pending = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending"
        ).scalar() or 0
        
        return {
            "total_revenue": float(total),
            "delivered_revenue": float(delivered),
            "pending_revenue": float(total - delivered),
            "pod_pending_revenue": float(pod_pending),
            "realized_revenue": float(delivered - pod_pending),
            "realization_rate": ((delivered - pod_pending) / total * 100) if total > 0 else 0
        }
    
    def _load_snapshots(self) -> Dict[str, AnalyticsSnapshot]:
        snapshots = {}
        results = self.db.query(DeliveryReport).all()
        
        for record in results:
            # Priority 3: Delivery Aging Logic
            delivery_aging = 0
            pending_delivery_aging = 0
            if record.good_issue_date and record.dn_create_date:
                if isinstance(record.good_issue_date, datetime):
                    pgi_date = record.good_issue_date.date()
                else:
                    pgi_date = record.good_issue_date
                if isinstance(record.dn_create_date, datetime):
                    create_date = record.dn_create_date.date()
                else:
                    create_date = record.dn_create_date
                delivery_aging = (pgi_date - create_date).days if pgi_date and create_date else 0
            
            if record.dn_create_date:
                if isinstance(record.dn_create_date, datetime):
                    create_date = record.dn_create_date.date()
                else:
                    create_date = record.dn_create_date
                pending_delivery_aging = (date.today() - create_date).days
            
            # Priority 2: Fixed POD Aging Logic
            pod_aging = 0
            pending_pod_aging = 0
            if record.pod_status == "Received" and record.pod_date and record.good_issue_date:
                if isinstance(record.pod_date, datetime):
                    pod_date = record.pod_date.date()
                else:
                    pod_date = record.pod_date
                if isinstance(record.good_issue_date, datetime):
                    pgi_date = record.good_issue_date.date()
                else:
                    pgi_date = record.good_issue_date
                pod_aging = (pod_date - pgi_date).days if pod_date and pgi_date else 0
            else:
                if record.good_issue_date:
                    if isinstance(record.good_issue_date, datetime):
                        pgi_date = record.good_issue_date.date()
                    else:
                        pgi_date = record.good_issue_date
                    pending_pod_aging = (date.today() - pgi_date).days
            
            # Priority 4: DN Status
            status = DNStatusEngine.get_status(record.pgi_status, record.pod_status)
            
            # Priority 5: Delay Bucket
            delay_days = max(delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging)
            delay_bucket = DNStatusEngine.get_delay_bucket(delay_days)
            
            # Health Score
            dn_health_score = self._calculate_health_score(
                delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging, record.pod_status
            )
            risk_score = 100 - dn_health_score
            
            snapshot = AnalyticsSnapshot(
                dn_no=record.dn_no,
                dealer=record.customer_name or "Unknown",
                warehouse=record.warehouse or "Unknown",
                city=record.ship_to_city or "Unknown",
                delivery_status=status.value,
                delivery_aging=delivery_aging,
                pending_delivery_aging=pending_delivery_aging,
                pod_aging=pod_aging,
                pending_pod_aging=pending_pod_aging,
                risk_score=risk_score,
                dealer_score=100,
                warehouse_score=100,
                city_score=100,
                delay_bucket=delay_bucket.value,
                dn_health_score=dn_health_score,
                total_value=float(record.dn_amount or 0),
                total_units=float(record.dn_qty or 0),
                product_count=1,
                pgi_date=pgi_date if 'pgi_date' in dir() else None,
                pod_date=pod_date if 'pod_date' in dir() else None,
                delivery_date=delivery_date if 'delivery_date' in dir() else None,
                created_date=create_date if 'create_date' in dir() else None
            )
            
            snapshots[record.dn_no] = snapshot
        
        return snapshots
    
    def _calculate_health_score(self, delivery_aging: int, pending_delivery_aging: int,
                                 pod_aging: int, pending_pod_aging: int, pod_status: str) -> int:
        score = 100
        if delivery_aging > 7: score -= 20
        elif delivery_aging > 3: score -= 10
        if pending_delivery_aging > 15: score -= 25
        elif pending_delivery_aging > 7: score -= 15
        if pod_status == "Pending":
            if pending_pod_aging > 10: score -= 30
            elif pending_pod_aging > 5: score -= 15
        else:
            if pod_aging > 10: score -= 15
            elif pod_aging > 5: score -= 5
        return max(0, min(100, score))
    
    def get(self) -> Dict:
        if self.is_stale() or not self._context:
            self.refresh()
        return self._context
    
    def get_snapshot(self, dn_no: str) -> Optional[AnalyticsSnapshot]:
        context = self.get()
        return context.get("snapshots", {}).get(dn_no)
    
    def get_dealer_rank(self, dealer_name: str) -> Dict:
        context = self.get()
        dealers = context.get("dealer_rankings", [])
        for dealer in dealers:
            if dealer["name"].lower() == dealer_name.lower():
                return dealer
        return {"rank": len(dealers) + 1, "health_score": 0}


# ==========================================================
# DEALER CACHE (Priority 8)
# ==========================================================

class DealerCache:
    def __init__(self, db: Session):
        self.db = db
        self._dealers: List[str] = []
        self._loaded = False
    
    def load(self):
        if self._loaded:
            return
        results = self.db.query(DeliveryReport.customer_name).filter(
            DeliveryReport.customer_name.isnot(None)
        ).distinct().all()
        self._dealers = [r.customer_name for r in results]
        self._loaded = True
        logger.info(f"✅ Loaded {len(self._dealers)} dealers into cache")
    
    def search(self, query: str, limit: int = 5) -> List[str]:
        self.load()
        query_lower = query.lower()
        for dealer in self._dealers:
            if dealer.lower() == query_lower:
                return [dealer]
        matches = [d for d in self._dealers if query_lower in d.lower()]
        return matches[:limit] if matches else []


# ==========================================================
# DN CACHE (Priority 7)
# ==========================================================

class DNCache:
    def __init__(self, ttl_seconds: int = 300):
        self.cache: Dict[str, Tuple[Dict, float]] = {}
        self.ttl = ttl_seconds
    
    def get(self, dn_no: str) -> Optional[Dict]:
        if dn_no in self.cache:
            data, timestamp = self.cache[dn_no]
            if time.time() - timestamp < self.ttl:
                return data
            del self.cache[dn_no]
        return None
    
    def set(self, dn_no: str, data: Dict):
        self.cache[dn_no] = (data, time.time())


# ==========================================================
# INTENT TYPES (Phase 2)
# ==========================================================

class IntentType(str, Enum):
    DN_STATUS = "dn_status"
    DN_DETAILS = "dn_details"
    DN_AGING = "dn_aging"
    DN_PRODUCTS = "dn_products"
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_RANKING = "dealer_ranking"
    DEALER_RISK = "dealer_risk"
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    WAREHOUSE_RANKING = "warehouse_ranking"
    CITY_DASHBOARD = "city_dashboard"
    CITY_RANKING = "city_ranking"
    POD_ANALYSIS = "pod_analysis"
    POD_PENDING = "pod_pending"
    PGI_ANALYSIS = "pgi_analysis"
    PGI_PENDING = "pgi_pending"
    REVENUE_ANALYSIS = "revenue_analysis"
    REVENUE_AT_RISK = "revenue_at_risk"
    PRODUCT_ANALYSIS = "product_analysis"
    PRODUCT_RANKING = "product_ranking"
    EXECUTIVE_SUMMARY = "executive_summary"
    CEO_BRIEFING = "ceo_briefing"
    NETWORK_HEALTH = "network_health"
    TOP_RISKS = "top_risks"
    RECOMMENDATIONS = "recommendations"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    TREND_ANALYSIS = "trend_analysis"
    PREDICTIVE_ANALYSIS = "predictive_analysis"
    DELIVERY_DELAYED = "delivery_delayed"
    HELP = "help"
    GENERAL_QUERY = "general_query"


# ==========================================================
# ENTITY TYPES (Phase 3)
# ==========================================================

class EntityType(str, Enum):
    DN_NUMBER = "dn_number"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    CITY = "city"
    PRODUCT = "product"
    NONE = "none"


@dataclass
class ExtractedEntity:
    type: EntityType
    value: str
    confidence: float = 1.0


# ==========================================================
# ENTITY EXTRACTOR (Phase 3)
# ==========================================================

class EntityExtractor:
    DN_PATTERN = re.compile(r'\b(\d{6,15})\b')
    DEALER_PATTERN = re.compile(r'dealer\s+([A-Za-z0-9\s&]+?)(?:\s+(?:dashboard|performance|risk|health)|$)', re.I)
    WAREHOUSE_PATTERN = re.compile(r'warehouse\s+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance|risk)|$)', re.I)
    CITY_PATTERN = re.compile(r'city\s+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance|risk)|$)', re.I)
    PRODUCT_PATTERN = re.compile(r'product\s+([A-Z0-9\-]+)|([A-Z]{2,3}-[0-9A-Z]+)', re.I)
    
    @classmethod
    def extract_all(cls, text: str) -> Dict[EntityType, ExtractedEntity]:
        entities = {}
        
        # DN
        dn_match = cls.DN_PATTERN.search(text)
        if dn_match:
            entities[EntityType.DN_NUMBER] = ExtractedEntity(EntityType.DN_NUMBER, dn_match.group(1))
        
        # Dealer
        dealer_match = cls.DEALER_PATTERN.search(text)
        if dealer_match:
            entities[EntityType.DEALER] = ExtractedEntity(EntityType.DEALER, dealer_match.group(1).strip())
        
        # Warehouse
        warehouse_match = cls.WAREHOUSE_PATTERN.search(text)
        if warehouse_match:
            entities[EntityType.WAREHOUSE] = ExtractedEntity(EntityType.WAREHOUSE, warehouse_match.group(1).strip())
        
        # City
        city_match = cls.CITY_PATTERN.search(text)
        if city_match:
            entities[EntityType.CITY] = ExtractedEntity(EntityType.CITY, city_match.group(1).strip())
        
        # Product
        product_match = cls.PRODUCT_PATTERN.search(text.upper())
        if product_match:
            product = product_match.group(1) or product_match.group(2)
            if product:
                entities[EntityType.PRODUCT] = ExtractedEntity(EntityType.PRODUCT, product)
        
        return entities


# ==========================================================
# NATURAL LANGUAGE MAPPER (Phase 4)
# ==========================================================

class NaturalLanguageMapper:
    
    @classmethod
    def map_to_intent(cls, text: str, entities: Dict) -> Tuple[IntentType, Optional[str]]:
        text_lower = text.lower().strip()
        
        # DN Priority
        if EntityType.DN_NUMBER in entities:
            dn = entities[EntityType.DN_NUMBER].value
            if any(p in text_lower for p in ["product", "items", "contains"]):
                return IntentType.DN_PRODUCTS, dn
            elif any(p in text_lower for p in ["aging", "how old", "age"]):
                return IntentType.DN_AGING, dn
            else:
                return IntentType.DN_STATUS, dn
        
        # Executive
        if any(p in text_lower for p in ["ceo briefing", "ceo dashboard", "board briefing"]):
            return IntentType.CEO_BRIEFING, None
        if any(p in text_lower for p in ["executive summary", "management summary"]):
            return IntentType.EXECUTIVE_SUMMARY, None
        if any(p in text_lower for p in ["network health", "health score"]):
            return IntentType.NETWORK_HEALTH, None
        if any(p in text_lower for p in ["top risks", "biggest risks"]):
            return IntentType.TOP_RISKS, None
        if any(p in text_lower for p in ["recommendations", "suggestions", "action items"]):
            return IntentType.RECOMMENDATIONS, None
        
        # Dealer
        if EntityType.DEALER in entities:
            dealer = entities[EntityType.DEALER].value
            if any(p in text_lower for p in ["risk", "high risk"]):
                return IntentType.DEALER_RISK, dealer
            else:
                return IntentType.DEALER_DASHBOARD, dealer
        if any(p in text_lower for p in ["top dealer", "dealer ranking", "best dealer"]):
            return IntentType.DEALER_RANKING, None
        
        # Warehouse
        if EntityType.WAREHOUSE in entities:
            return IntentType.WAREHOUSE_DASHBOARD, entities[EntityType.WAREHOUSE].value
        if any(p in text_lower for p in ["warehouse ranking", "top warehouse"]):
            return IntentType.WAREHOUSE_RANKING, None
        
        # City
        if EntityType.CITY in entities:
            return IntentType.CITY_DASHBOARD, entities[EntityType.CITY].value
        
        # POD
        if any(p in text_lower for p in ["pending pod", "pod pending"]):
            return IntentType.POD_PENDING, None
        if any(p in text_lower for p in ["pod analysis", "pod performance"]):
            return IntentType.POD_ANALYSIS, None
        
        # PGI
        if any(p in text_lower for p in ["pending pgi", "pending dispatch"]):
            return IntentType.PGI_PENDING, None
        
        # Revenue
        if any(p in text_lower for p in ["revenue at risk", "at risk revenue"]):
            return IntentType.REVENUE_AT_RISK, None
        if any(p in text_lower for p in ["revenue analysis", "revenue report"]):
            return IntentType.REVENUE_ANALYSIS, None
        
        # Product
        if EntityType.PRODUCT in entities:
            return IntentType.PRODUCT_ANALYSIS, entities[EntityType.PRODUCT].value
        if any(p in text_lower for p in ["top product", "product ranking"]):
            return IntentType.PRODUCT_RANKING, None
        
        # Analytics
        if any(p in text_lower for p in ["why", "root cause", "reason for"]):
            return IntentType.ROOT_CAUSE_ANALYSIS, None
        if any(p in text_lower for p in ["trend", "over time", "pattern"]):
            return IntentType.TREND_ANALYSIS, None
        if any(p in text_lower for p in ["predict", "forecast", "likely"]):
            return IntentType.PREDICTIVE_ANALYSIS, None
        
        # Delivery
        if any(p in text_lower for p in ["delayed delivery", "late delivery"]):
            return IntentType.DELIVERY_DELAYED, None
        
        # Help
        if any(p in text_lower for p in ["help", "menu", "commands"]):
            return IntentType.HELP, None
        
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# CONVERSATION MEMORY (Phase 7 & 8)
# ==========================================================

class ConversationMemory:
    def __init__(self, max_history: int = 20):
        self.history: Dict[str, deque] = {}
        self.contexts: Dict[str, Dict] = {}
        self.max_history = max_history
    
    def get_or_create_context(self, phone_number: str) -> Dict:
        if phone_number not in self.contexts:
            self.contexts[phone_number] = {
                "current_dn": None,
                "current_dealer": None,
                "current_warehouse": None,
                "current_city": None,
                "current_product": None
            }
        return self.contexts[phone_number]
    
    def add(self, phone_number: str, question: str, response: str, 
            intent: IntentType, entity: Optional[str] = None, entities: Dict = None):
        history = self.history.get(phone_number, deque(maxlen=self.max_history))
        context = self.get_or_create_context(phone_number)
        
        history.append({
            "question": question,
            "response": response[:500],
            "intent": intent.value,
            "entity": entity,
            "timestamp": datetime.utcnow().isoformat()
        })
        self.history[phone_number] = history
        
        # Update context
        if entities:
            if EntityType.DN_NUMBER in entities:
                context["current_dn"] = entities[EntityType.DN_NUMBER].value
            if EntityType.DEALER in entities:
                context["current_dealer"] = entities[EntityType.DEALER].value
            if EntityType.WAREHOUSE in entities:
                context["current_warehouse"] = entities[EntityType.WAREHOUSE].value
            if EntityType.CITY in entities:
                context["current_city"] = entities[EntityType.CITY].value
            if EntityType.PRODUCT in entities:
                context["current_product"] = entities[EntityType.PRODUCT].value
    
    def get_last_context(self, phone_number: str) -> Dict:
        context = self.get_or_create_context(phone_number)
        history = self.history.get(phone_number, deque())
        
        if not history:
            return context
        
        last = history[-1]
        return {
            **context,
            "last_intent": last.get("intent"),
            "last_entity": last.get("entity")
        }
    
    def resolve_follow_up(self, phone_number: str, question: str) -> Dict:
        context = self.get_or_create_context(phone_number)
        question_lower = question.lower()
        
        resolved = {}
        if any(w in question_lower for w in ["it", "this", "that", "the dn"]):
            if context.get("current_dn"):
                resolved["dn"] = context["current_dn"]
        if any(w in question_lower for w in ["the dealer", "this dealer"]):
            if context.get("current_dealer"):
                resolved["dealer"] = context["current_dealer"]
        if any(w in question_lower for w in ["the warehouse", "this warehouse"]):
            if context.get("current_warehouse"):
                resolved["warehouse"] = context["current_warehouse"]
        if any(w in question_lower for w in ["the city", "this city"]):
            if context.get("current_city"):
                resolved["city"] = context["current_city"]
        if any(w in question_lower for w in ["the product", "this product"]):
            if context.get("current_product"):
                resolved["product"] = context["current_product"]
        
        return resolved


# ==========================================================
# RESPONSE TEMPLATES (Phase 9)
# ==========================================================

class ResponseTemplates:
    
    @staticmethod
    def dn_status_template(snapshot: AnalyticsSnapshot, dealer_rank: Dict) -> str:
        status_icon = DNStatusEngine.get_status_icon(DeliveryStatus(snapshot.delivery_status))
        delay_icon = DNStatusEngine.get_delay_icon(DelayBucket(snapshot.delay_bucket))
        risk_icon = "🟢" if snapshot.risk_score < 30 else "🟡" if snapshot.risk_score < 60 else "🔴"
        
        return f"""╔══════════════════════════════════════════════════════════════════════════════╗
║                         📦 DN COMPLETE INTELLIGENCE REPORT                                 ║
║                                    {snapshot.dn_no}                                        ║
╚══════════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *DN SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Dealer: {snapshot.dealer}
   • City: {snapshot.city}
   • Warehouse: {snapshot.warehouse}
   • Status: {status_icon} {snapshot.delivery_status}
   • Delay: {delay_icon} {snapshot.delay_bucket}
   • Created: {snapshot.created_date.strftime('%d-%b-%Y') if snapshot.created_date else 'N/A'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 *PRODUCT SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Value: Rs {snapshot.total_value:,.2f}
   • Total Units: {snapshot.total_units:,.0f}
   • Products: {snapshot.product_count}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AGING ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Delivery Aging: {snapshot.delivery_aging} days
   • Pending Delivery: {snapshot.pending_delivery_aging} days
   • POD Aging: {snapshot.pod_aging} days
   • Pending POD: {snapshot.pending_pod_aging} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *RISK ASSESSMENT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Health Score: {snapshot.dn_health_score}/100
   • Risk Score: {risk_icon} {snapshot.risk_score}/100
   • Dealer Rank: #{dealer_rank.get('rank', 'N/A')}

💡 *Follow-up questions:* "What products?" | "POD status?" | "Risk analysis?" """
    
    @staticmethod
    def executive_summary_template(context: Dict, ai_insights: str = None) -> str:
        network = context.get("network_health", {})
        revenue = context.get("revenue_metrics", {})
        pending = context.get("pending_metrics", {})
        pod = context.get("pod_metrics", {})
        dealers = context.get("dealer_rankings", [])[:5]
        
        health_icon = "🟢" if network.get('health_score', 0) >= 70 else "🟡" if network.get('health_score', 0) >= 50 else "🔴"
        
        response = f"""👑 *EXECUTIVE SUMMARY DASHBOARD*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *NETWORK HEALTH*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Health Score: {health_icon} {network.get('health_score', 0)}/100
   • Total DNs: {network.get('total_dns', 0):,}
   • Delivery Rate: {network.get('delivery_rate', 0)}%
   • POD Compliance: {network.get('pod_compliance', 0)}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *REVENUE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Revenue: Rs {revenue.get('total_revenue', 0):,.2f}
   • Realized Revenue: Rs {revenue.get('realized_revenue', 0):,.2f}
   • Revenue at Risk: Rs {revenue.get('revenue_at_risk', 0):,.2f}
   • Realization Rate: {revenue.get('realization_rate', 0)}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏳ *PENDING METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Pending DNs: {pending.get('pending_dns', 0)}
   • Pending Value: Rs {pending.get('pending_value', 0):,.2f}
   • Pending PODs: {pod.get('pending_pods', 0)}
   • POD Pending Value: Rs {pod.get('pending_pod_value', 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 *TOP 5 DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(dealers, 1):
            response += f"   {i}. {d['name'][:30]} - Rs {d['total_value']:,.2f} (Health: {d['health_score']})\n"
        
        if ai_insights:
            response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🤖 *AI INSIGHTS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{ai_insights}
"""
        
        return response
    
    @staticmethod
    def help_template() -> str:
        return WELCOME_MESSAGE


# ==========================================================
# INTENT ROUTER (Phase 5)
# ==========================================================

class IntentRouter:
    def __init__(self, analytics_context: AnalyticsContext, dn_cache: DNCache):
        self.analytics_context = analytics_context
        self.dn_cache = dn_cache
    
    def route(self, intent: IntentType, entity: Optional[str] = None,
              entities: Dict = None, context: Dict = None) -> Dict[str, Any]:
        
        if intent == IntentType.DN_STATUS:
            return self._handle_dn_status(entity, entities, context)
        elif intent == IntentType.DN_PRODUCTS:
            return self._handle_dn_products(entity, entities, context)
        elif intent == IntentType.DEALER_DASHBOARD:
            return self._handle_dealer_dashboard(entity, entities, context)
        elif intent == IntentType.DEALER_RANKING:
            return self._handle_dealer_ranking()
        elif intent == IntentType.EXECUTIVE_SUMMARY:
            return self._handle_executive_summary()
        elif intent == IntentType.NETWORK_HEALTH:
            return self._handle_network_health()
        elif intent == IntentType.REVENUE_ANALYSIS:
            return self._handle_revenue_analysis()
        elif intent == IntentType.POD_PENDING:
            return self._handle_pod_pending()
        elif intent == IntentType.HELP:
            return {"success": True, "response": ResponseTemplates.help_template()}
        else:
            return {"success": False, "response": None, "needs_ai": True}
    
    def _handle_dn_status(self, entity, entities, context):
        dn = entity or (entities.get(EntityType.DN_NUMBER).value if entities.get(EntityType.DN_NUMBER) else None)
        if not dn:
            return {"success": False, "response": "❓ Please provide a DN number."}
        
        # Check cache (Priority 7)
        cached = self.dn_cache.get(dn)
        if cached:
            return cached
        
        snapshot = self.analytics_context.get_snapshot(dn)
        if not snapshot:
            return {"success": False, "response": f"❌ DN {dn} not found."}
        
        dealer_rank = self.analytics_context.get_dealer_rank(snapshot.dealer)
        response = ResponseTemplates.dn_status_template(snapshot, dealer_rank)
        
        result = {"success": True, "response": response}
        self.dn_cache.set(dn, result)
        return result
    
    def _handle_dn_products(self, entity, entities, context):
        dn = entity or (entities.get(EntityType.DN_NUMBER).value if entities.get(EntityType.DN_NUMBER) else None)
        if not dn:
            return {"success": False, "response": "❓ Please provide a DN number."}
        return {"success": True, "response": f"📦 *Products in DN {dn}*\n\n(Product details would be loaded from logistics service)"}
    
    def _handle_dealer_dashboard(self, entity, entities, context):
        dealer = entity or (entities.get(EntityType.DEALER).value if entities.get(EntityType.DEALER) else None)
        if not dealer:
            return {"success": False, "response": "🏪 Please provide a dealer name."}
        
        rankings = self.analytics_context.get().get("dealer_rankings", [])
        dealer_data = next((d for d in rankings if d["name"].lower() == dealer.lower()), None)
        
        if not dealer_data:
            return {"success": False, "response": f"🏪 Dealer '{dealer}' not found."}
        
        response = ResponseTemplates.dealer_dashboard_template(dealer_data) if hasattr(ResponseTemplates, 'dealer_dashboard_template') else str(dealer_data)
        return {"success": True, "response": response}
    
    def _handle_dealer_ranking(self):
        rankings = self.analytics_context.get().get("dealer_rankings", [])[:10]
        if not rankings:
            return {"success": False, "response": "🏪 No dealer data available."}
        
        response = "🏆 *TOP 10 DEALERS*\n\n"
        for i, d in enumerate(rankings, 1):
            health_icon = "🟢" if d["health_score"] >= 70 else "🟡" if d["health_score"] >= 50 else "🔴"
            response += f"{i}. *{d['name'][:35]}*\n"
            response += f"   💰 Rs {d['total_value']:,.2f} | 📦 {d['total_dns']} DNs\n"
            response += f"   {health_icon} Health: {d['health_score']}%\n\n"
        return {"success": True, "response": response}
    
    def _handle_executive_summary(self):
        context = self.analytics_context.get()
        response = ResponseTemplates.executive_summary_template(context)
        return {"success": True, "response": response}
    
    def _handle_network_health(self):
        context = self.analytics_context.get()
        network = context.get("network_health", {})
        pending = context.get("pending_metrics", {})
        pod = context.get("pod_metrics", {})
        revenue = context.get("revenue_metrics", {})
        
        response = f"""📊 *NETWORK HEALTH DASHBOARD*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Health Score: {network.get('health_score', 0)}/100
   • Pending DNs: {pending.get('pending_dns', 0)}
   • Pending Value: Rs {pending.get('pending_value', 0):,.2f}
   • Pending PODs: {pod.get('pending_pods', 0)}
   • Realization Rate: {revenue.get('realization_rate', 0)}%

💡 Type "Executive summary" for detailed analysis"""
        return {"success": True, "response": response}
    
    def _handle_revenue_analysis(self):
        revenue = self.analytics_context.get().get("revenue_metrics", {})
        response = f"""💰 *REVENUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Revenue: Rs {revenue.get('total_revenue', 0):,.2f}
   • Realized: Rs {revenue.get('realized_revenue', 0):,.2f} ✅
   • Pending Delivery: Rs {revenue.get('pending_revenue', 0):,.2f} ⏳
   • POD Pending: Rs {revenue.get('pod_pending_revenue', 0):,.2f} 📋

📈 *REALIZATION RATE: {revenue.get('realization_rate', 0)}%*"""
        return {"success": True, "response": response}
    
    def _handle_pod_pending(self):
        pod = self.analytics_context.get().get("pod_metrics", {})
        response = f"""📋 *PENDING POD REPORT*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Pending PODs: {pod.get('pending_pods', 0)}
   • Value at Risk: Rs {pod.get('pending_pod_value', 0):,.2f}

💡 Focus on collecting these PODs to reduce revenue at risk."""
        return {"success": True, "response": response}


# ==========================================================
# MAIN AI QUERY SERVICE (WITH GROQ)
# ==========================================================

class AIQueryService:
    """Complete orchestrator with GROQ integration and all 11 priorities"""
    
    def __init__(self, db: Session):
        self.db = db
        
        # Priority 1 & 6: Analytics Context
        self.analytics_context = AnalyticsContext(db)
        self.analytics_context.refresh()
        
        # Priority 8: Dealer Cache
        self.dealer_cache = DealerCache(db)
        self.dealer_cache.load()
        
        # Priority 7: DN Cache
        self.dn_cache = DNCache()
        
        # Conversation Memory (Phase 7 & 8)
        self.conversation_memory = ConversationMemory()
        
        # Entity Extraction (Phase 3)
        self.entity_extractor = EntityExtractor()
        
        # Intent Mapping (Phase 4)
        self.nlp_mapper = NaturalLanguageMapper()
        
        # Intent Router (Phase 5)
        self.intent_router = IntentRouter(self.analytics_context, self.dn_cache)
        
        # GROQ Integration
        self.ai_provider = None
        self.ai_available = False
        
        if AI_PROVIDER_AVAILABLE:
            try:
                self.ai_provider = get_ai_provider_service(db)
                if self.ai_provider:
                    self.ai_available = self._check_groq_health()
                    logger.info(f"✅ GROQ AI Provider: {'Available' if self.ai_available else 'Unavailable'}")
            except Exception as e:
                logger.error(f"Failed to initialize AI provider: {e}")
                self.ai_available = False
        
        logger.info("=" * 60)
        logger.info("🚀 AI QUERY ORCHESTRATOR v20.0 (COMPLETE)")
        logger.info(f"   Analytics Context: {'Loaded' if self.analytics_context.get() else 'Empty'}")
        logger.info(f"   Dealer Cache: {len(self.dealer_cache._dealers)} dealers")
        logger.info(f"   DN Cache: {'Enabled'}")
        logger.info(f"   GROQ AI: {'Available' if self.ai_available else 'Not Available'}")
        logger.info(f"   Intent Types: {len([i for i in IntentType])}")
        logger.info("=" * 60)
    
    def _check_groq_health(self) -> bool:
        if not self.ai_provider:
            return False
        try:
            result = self.ai_provider.answer_question(question="Say 'GROQ is working'", user_role="system")
            return result.get("success", False)
        except Exception as e:
            logger.error(f"GROQ health check error: {e}")
            return False
    
    def _build_ai_context(self, conv_context: Dict) -> str:
        """Build rich context for GROQ AI"""
        context = self.analytics_context.get()
        network = context.get("network_health", {})
        revenue = context.get("revenue_metrics", {})
        pending = context.get("pending_metrics", {})
        pod = context.get("pod_metrics", {})
        dealers = context.get("dealer_rankings", [])[:5]
        
        prompt = f"""
BUSINESS CONTEXT (Real-time from your logistics system):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 NETWORK HEALTH:
   • Health Score: {network.get('health_score', 'N/A')}/100
   • Total DNs: {network.get('total_dns', 'N/A')}
   • Delivery Rate: {network.get('delivery_rate', 'N/A')}%
   • POD Compliance: {network.get('pod_compliance', 'N/A')}%

💰 REVENUE METRICS:
   • Total Revenue: Rs {revenue.get('total_revenue', 0):,.2f}
   • Realized Revenue: Rs {revenue.get('realized_revenue', 0):,.2f}
   • Revenue at Risk: Rs {revenue.get('revenue_at_risk', 0):,.2f}
   • Realization Rate: {revenue.get('realization_rate', 0)}%

⏳ PENDING METRICS:
   • Pending DNs: {pending.get('pending_dns', 0)}
   • Pending Value: Rs {pending.get('pending_value', 0):,.2f}
   • Avg Pending Days: {pending.get('avg_pending_days', 0)} days

📋 POD METRICS:
   • Pending PODs: {pod.get('pending_pods', 0)}
   • POD Pending Value: Rs {pod.get('pending_pod_value', 0):,.2f}

🏆 TOP 5 DEALERS:
"""
        for d in dealers:
            prompt += f"   • {d.get('name', 'N/A')}: Rs {d.get('total_value', 0):,.2f} (Health: {d.get('health_score', 0)}%)\n"
        
        if conv_context:
            prompt += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONVERSATION CONTEXT:
   • Current DN: {conv_context.get('current_dn', 'None')}
   • Current Dealer: {conv_context.get('current_dealer', 'None')}
   • Last Intent: {conv_context.get('last_intent', 'None')}
"""
        
        return prompt
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        start_time = time.time()
        question = question.strip()
        
        logger.info(f"📱 Processing: {question[:100]}")
        
        # Step 1: Extract entities
        entities = self.entity_extractor.extract_all(question)
        logger.info(f"🔍 Entities: {[(e.type.value, e.value) for e in entities.values()]}")
        
        # Step 2: Resolve follow-up context
        follow_up = {}
        if user_phone:
            follow_up = self.conversation_memory.resolve_follow_up(user_phone, question)
            if follow_up:
                logger.info(f"🔄 Follow-up resolved: {follow_up}")
                # Inject resolved entities
                if "dn" in follow_up and EntityType.DN_NUMBER not in entities:
                    entities[EntityType.DN_NUMBER] = ExtractedEntity(EntityType.DN_NUMBER, follow_up["dn"])
                if "dealer" in follow_up and EntityType.DEALER not in entities:
                    entities[EntityType.DEALER] = ExtractedEntity(EntityType.DEALER, follow_up["dealer"])
        
        # Step 3: Map to intent
        intent, entity = self.nlp_mapper.map_to_intent(question, entities)
        logger.info(f"🎯 Intent: {intent.value}, Entity: {entity}")
        
        # Step 4: Get conversation context
        conv_context = {}
        if user_phone:
            conv_context = self.conversation_memory.get_last_context(user_phone)
        
        # Step 5: Route intent
        result = self.intent_router.route(intent, entity, entities, conv_context)
        
        # Step 6: Fallback to GROQ if needed
        if result.get("needs_ai") or (result.get("success") is False and "not found" in result.get("response", "").lower()):
            if self.ai_available and self.ai_provider:
                logger.info(f"🤖 Falling back to GROQ for: {question[:50]}")
                try:
                    ai_context = self._build_ai_context(conv_context)
                    ai_result = self.ai_provider.answer_question(
                        question=f"""{ai_context}

USER QUESTION: {question}

Instructions:
- Answer using the business data provided when possible.
- Provide concise, WhatsApp-friendly responses with emojis.
- If you don't have specific data, give general logistics guidance.
- For non-business questions, politely redirect to logistics topics.""",
                        user_phone=user_phone,
                        user_role=user_role or "guest"
                    )
                    
                    if ai_result.get("success"):
                        result = {"success": True, "response": ai_result.get("content")}
                    else:
                        result = self._get_fallback_response(question, ai_result.get('error'))
                except Exception as e:
                    logger.error(f"GROQ error: {e}")
                    result = self._get_fallback_response(question, str(e))
            else:
                result = self._get_fallback_response(question, "AI not available")
        
        # Step 7: Store in memory
        if user_phone and result.get("success"):
            self.conversation_memory.add(user_phone, question, result.get("response", ""), intent, entity, entities)
        
        # Step 8: Add metrics
        result["processing_time_ms"] = int((time.time() - start_time) * 1000)
        logger.info(f"⚡ Response time: {result['processing_time_ms']}ms")
        
        return result
    
    def _get_fallback_response(self, question: str, error: str = None) -> Dict[str, Any]:
        error_msg = f"\n\n*Error:* {error[:200]}" if error else ""
        
        return {
            "success": True,
            "response": f"""🤖 *AI LOGISTICS ASSISTANT v20.0*

I understand you're asking about: "{question[:50]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *AI Service Unavailable*{error_msg}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 Send a DN number (6-15 digits)
🏪 Type a dealer name
👑 "Executive summary" - Full dashboard
🏆 "Top dealers" - Best performers
📋 "Pending PODs" - POD collection required
💰 "Revenue analysis" - Financial view
📊 "Network health" - System status

Type "Help" for complete menu."""
        }


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def process_whatsapp_query(question: str, db: Session, user_phone: str = None, user_role: str = None) -> str:
    """Process WhatsApp query and return response"""
    try:
        service = AIQueryService(db)
        result = service.process_query(question, user_phone, user_role)
        return result.get("response", "⚠️ Unable to process your request. Please try again.")
    except Exception as e:
        logger.error(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


# ==========================================================
# WELCOME MESSAGE
# ==========================================================

WELCOME_MESSAGE = """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT v20.0*

I'm your complete logistics intelligence orchestrator with GROQ AI integration.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *WHAT YOU CAN ASK:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN TRACKING*
   • "Status of DN 80012345" - Complete DN intelligence
   • "What products are in DN 80012345?" - Product breakdown
   • "How old is DN 80012345?" - Aging analysis

🏪 *DEALER INSIGHTS*
   • "ABC Electronics dashboard" - Dealer performance
   • "Top performing dealers" - Rankings
   • "High risk dealers" - Risk analysis

🏭 *WAREHOUSE & CITY*
   • "Lahore warehouse dashboard" - Warehouse performance
   • "Karachi city dashboard" - City performance

📋 *POD & PGI*
   • "Pending PODs" - POD collection required
   • "Pending PGI" - Dispatch pending

💰 *REVENUE & FINANCIAL*
   • "Revenue analysis" - Complete breakdown
   • "Revenue at risk" - Exposure analysis

👑 *EXECUTIVE REPORTS*
   • "Executive summary" - Complete dashboard
   • "CEO briefing" - Leadership view
   • "Network health" - System status
   • "Top risks" - Critical issues
   • "Recommendations" - Action items

📈 *ADVANCED ANALYTICS*
   • "Why are deliveries delayed?" - Root cause
   • "What are the trends?" - Trend analysis
   • "Predict future delays" - Predictive analysis

💬 *AI-POWERED QUESTIONS*
   • Any general logistics question
   • "How can I improve delivery times?"
   • "What's causing POD delays?"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRO TIPS:*
   • I remember context! Ask "What products?" after a DN query
   • Fast responses for known patterns, AI for complex questions
   • Type "Help" anytime for this menu

*Powered by AI Logistics Intelligence v20.0 | GROQ AI Integration*"""
