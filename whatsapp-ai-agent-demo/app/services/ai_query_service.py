# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v18.0 - ANALYTICS SNAPSHOT)
# ==========================================================
# DN-CENTRIC INTELLIGENCE ENGINE v18.0:
# - ANALYTICS SNAPSHOT TABLE (Priority 1)
# - FIXED POD AGING LOGIC (Priority 2)
# - DELIVERY AGING LOGIC (Priority 3)
# - DN STATUS ENGINE (Priority 4)
# - DELAY BUCKETS (Priority 5)
# - SHARED ANALYTICS CONTEXT (Priority 6)
# - DN LOOKUP CACHE (Priority 7)
# - DEALER STARTUP CACHE (Priority 8)
# - PRODUCT INTELLIGENCE ENGINE (Priority 9)
# - NATURAL LANGUAGE ANALYTICS FIELDS (Priority 10)
# - FIXED CEO BRIEFING BUG (Priority 11)
# ==========================================================

import re
import time
import hashlib
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, date
from enum import Enum
from collections import deque, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, and_, or_, text
from loguru import logger

from app.config import config
from app.models import DeliveryReport

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
        """Get DN status based on PGI and POD status"""
        if pgi_status != "Completed":
            return DeliveryStatus.OPEN
        elif pgi_status == "Completed" and pod_status != "Received":
            return DeliveryStatus.IN_TRANSIT
        elif pod_status == "Received":
            return DeliveryStatus.DELIVERED
        return DeliveryStatus.OPEN
    
    @staticmethod
    def get_delay_bucket(days_delayed: int) -> DelayBucket:
        """Get delay bucket classification (Priority 5)"""
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
        """Get icon for status"""
        icons = {
            DeliveryStatus.OPEN: "📝",
            DeliveryStatus.IN_TRANSIT: "🚚",
            DeliveryStatus.DELIVERED: "✅",
            DeliveryStatus.CLOSED: "🔒"
        }
        return icons.get(status, "❓")
    
    @staticmethod
    def get_delay_icon(bucket: DelayBucket) -> str:
        """Get icon for delay bucket"""
        icons = {
            DelayBucket.ON_TIME: "🟢",
            DelayBucket.MINOR_DELAY: "🟡",
            DelayBucket.MODERATE_DELAY: "🟠",
            DelayBucket.CRITICAL: "🔴",
            DelayBucket.SEVERE: "💀"
        }
        return icons.get(bucket, "⚪")


# ==========================================================
# ANALYTICS CONTEXT CACHE (Priority 6)
# ==========================================================

class AnalyticsContext:
    """Single source of truth for all analytics data - calculated once, reused everywhere"""
    
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
        """Refresh all analytics data from database"""
        start_time = time.time()
        logger.info("🔄 Refreshing analytics context...")
        
        self._context = {
            "pending_metrics": self._get_pending_metrics(),
            "pod_metrics": self._get_pod_metrics(),
            "dealer_rankings": self._get_dealer_rankings(),
            "warehouse_rankings": self._get_warehouse_rankings(),
            "city_rankings": self._get_city_rankings(),
            "product_metrics": self._get_product_metrics(),
            "division_metrics": self._get_division_metrics(),
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
        """Get product intelligence metrics (Priority 9)"""
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
    
    def _get_division_metrics(self) -> List[Dict]:
        """Get division-wise metrics"""
        products = self._get_product_metrics()
        division_data = defaultdict(lambda: {"quantity": 0, "value": 0, "dns": 0})
        
        # This would need product-to-division mapping
        # Simplified version using keyword matching
        for p in products.get("top_products", []):
            division = self._extract_division(p["name"])
            division_data[division]["quantity"] += p["total_qty"]
            division_data[division]["value"] += p["total_value"]
            division_data[division]["dns"] += p["total_dns"]
        
        return [{"division": k, **v} for k, v in division_data.items()]
    
    def _extract_division(self, product_name: str) -> str:
        """Extract division from product name"""
        if not product_name:
            return "Other"
        
        product_upper = product_name.upper()
        
        if any(k in product_upper for k in ["AC", "AIR CONDITIONER", "HSU"]):
            return "AC"
        elif any(k in product_upper for k in ["TV", "LED", "LCD"]):
            return "TV"
        elif any(k in product_upper for k in ["REF", "FRIDGE", "REFRIGERATOR", "HRF"]):
            return "Refrigerator"
        elif any(k in product_upper for k in ["WM", "WASHING", "HWM"]):
            return "Washing Machine"
        else:
            return "Other"
    
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
        """Load all analytics snapshots from database"""
        snapshots = {}
        
        results = self.db.query(DeliveryReport).all()
        
        for record in results:
            # Calculate delivery aging (Priority 3)
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
            
            # Calculate POD aging (Priority 2 - FIXED)
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
                # Pending POD aging = today - PGI date
                if record.good_issue_date:
                    if isinstance(record.good_issue_date, datetime):
                        pgi_date = record.good_issue_date.date()
                    else:
                        pgi_date = record.good_issue_date
                    pending_pod_aging = (date.today() - pgi_date).days
            
            # Get status from DN Status Engine
            status = DNStatusEngine.get_status(record.pgi_status, record.pod_status)
            
            # Calculate delay bucket
            delay_days = max(delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging)
            delay_bucket = DNStatusEngine.get_delay_bucket(delay_days)
            
            # Calculate scores
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
                dealer_score=100,  # Will be populated from rankings
                warehouse_score=100,
                city_score=100,
                delay_bucket=delay_bucket.value,
                dn_health_score=dn_health_score,
                total_value=float(record.dn_amount or 0),
                total_units=float(record.dn_qty or 0),
                product_count=1
            )
            
            snapshots[record.dn_no] = snapshot
        
        return snapshots
    
    def _calculate_health_score(self, delivery_aging: int, pending_delivery_aging: int,
                                 pod_aging: int, pending_pod_aging: int, pod_status: str) -> int:
        score = 100
        
        if delivery_aging > 7:
            score -= 20
        elif delivery_aging > 3:
            score -= 10
        
        if pending_delivery_aging > 15:
            score -= 25
        elif pending_delivery_aging > 7:
            score -= 15
        
        if pod_status == "Pending":
            if pending_pod_aging > 10:
                score -= 30
            elif pending_pod_aging > 5:
                score -= 15
        else:
            if pod_aging > 10:
                score -= 15
            elif pod_aging > 5:
                score -= 5
        
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
    
    def get_warehouse_score(self, warehouse_name: str) -> int:
        context = self.get()
        warehouses = context.get("warehouse_rankings", [])
        for i, w in enumerate(warehouses):
            if w["name"].lower() == warehouse_name.lower():
                return max(0, 100 - (i * 5))
        return 50


# ==========================================================
# DEALER CACHE (Priority 8)
# ==========================================================

class DealerCache:
    """Startup dealer cache for lightning-fast fuzzy search"""
    
    def __init__(self, db: Session):
        self.db = db
        self._dealers: List[str] = []
        self._dealer_index: Dict[str, List[str]] = {}
        self._loaded = False
    
    def load(self):
        """Load all dealers at startup"""
        if self._loaded:
            return
        
        results = self.db.query(DeliveryReport.customer_name).filter(
            DeliveryReport.customer_name.isnot(None)
        ).distinct().all()
        
        self._dealers = [r.customer_name for r in results]
        
        # Build trigram index for fuzzy search
        for dealer in self._dealers:
            dealer_lower = dealer.lower()
            for i in range(len(dealer_lower) - 2):
                trigram = dealer_lower[i:i+3]
                if trigram not in self._dealer_index:
                    self._dealer_index[trigram] = []
                self._dealer_index[trigram].append(dealer)
        
        self._loaded = True
        logger.info(f"✅ Loaded {len(self._dealers)} dealers into cache")
    
    def search(self, query: str, limit: int = 5) -> List[str]:
        """Fuzzy search dealers using trigram matching"""
        self.load()
        
        query_lower = query.lower()
        
        # Exact match first
        for dealer in self._dealers:
            if dealer.lower() == query_lower:
                return [dealer]
        
        # Partial match
        matches = []
        for dealer in self._dealers:
            if query_lower in dealer.lower():
                matches.append(dealer)
        
        if matches:
            return matches[:limit]
        
        # Trigram search
        trigrams = []
        for i in range(len(query_lower) - 2):
            trigrams.append(query_lower[i:i+3])
        
        scores = defaultdict(int)
        for trigram in trigrams:
            if trigram in self._dealer_index:
                for dealer in self._dealer_index[trigram]:
                    scores[dealer] += 1
        
        scored_matches = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [m[0] for m in scored_matches[:limit]]


# ==========================================================
# DN CACHE (Priority 7)
# ==========================================================

class DNCache:
    """DN lookup cache with TTL"""
    
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
    
    def invalidate(self, dn_no: str):
        if dn_no in self.cache:
            del self.cache[dn_no]


# ==========================================================
# PRODUCT INTELLIGENCE ENGINE (Priority 9)
# ==========================================================

class ProductIntelligenceEngine:
    """Complete product intelligence with fill rates and pending analysis"""
    
    def __init__(self, db: Session, analytics_context: AnalyticsContext):
        self.db = db
        self.analytics_context = analytics_context
    
    def get_product_dashboard(self, product_name: str) -> Dict[str, Any]:
        """Get complete product dashboard with ordered, delivered, pending quantities"""
        
        results = self.db.query(DeliveryReport).filter(
            DeliveryReport.product == product_name
        ).all()
        
        if not results:
            return {"success": False, "message": f"Product '{product_name}' not found"}
        
        ordered_qty = sum(float(r.dn_qty or 0) for r in results)
        delivered_qty = sum(float(r.dn_qty or 0) for r in results if r.pgi_status == "Completed")
        pending_qty = ordered_qty - delivered_qty
        
        ordered_value = sum(float(r.dn_amount or 0) for r in results)
        delivered_value = sum(float(r.dn_amount or 0) for r in results if r.pgi_status == "Completed")
        pending_value = ordered_value - delivered_value
        
        fill_rate = (delivered_qty / ordered_qty * 100) if ordered_qty > 0 else 0
        
        # Get monthly trend
        monthly_data = defaultdict(lambda: {"ordered": 0, "delivered": 0})
        for r in results:
            if r.dn_create_date:
                if isinstance(r.dn_create_date, datetime):
                    month_key = r.dn_create_date.strftime("%Y-%m")
                else:
                    month_key = str(r.dn_create_date)[:7]
                monthly_data[month_key]["ordered"] += float(r.dn_qty or 0)
                if r.pgi_status == "Completed":
                    monthly_data[month_key]["delivered"] += float(r.dn_qty or 0)
        
        # Get top dealers for this product
        dealer_data = defaultdict(lambda: {"quantity": 0, "value": 0})
        for r in results:
            if r.customer_name:
                dealer_data[r.customer_name]["quantity"] += float(r.dn_qty or 0)
                dealer_data[r.customer_name]["value"] += float(r.dn_amount or 0)
        
        top_dealers = sorted(dealer_data.items(), key=lambda x: x[1]["quantity"], reverse=True)[:5]
        
        return {
            "success": True,
            "product_name": product_name,
            "ordered_qty": ordered_qty,
            "delivered_qty": delivered_qty,
            "pending_qty": pending_qty,
            "ordered_value": ordered_value,
            "delivered_value": delivered_value,
            "pending_value": pending_value,
            "fill_rate": round(fill_rate, 1),
            "total_dns": len(set(r.dn_no for r in results)),
            "monthly_trend": dict(monthly_data),
            "top_dealers": top_dealers
        }
    
    def get_product_ranking(self, metric: str = "value", limit: int = 10) -> List[Dict]:
        """Get ranked products by various metrics"""
        results = self.db.query(
            DeliveryReport.product,
            func.sum(DeliveryReport.dn_qty).label("total_qty"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(DeliveryReport.dn_no).label("total_dns"),
            func.sum(DeliveryReport.dn_qty).filter(DeliveryReport.pgi_status == "Completed").label("delivered_qty")
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
                "quantity": float(r.total_qty or 0),
                "value": float(r.total_value or 0),
                "dns": r.total_dns,
                "fill_rate": round(fill_rate, 1)
            })
        
        if metric == "value":
            products.sort(key=lambda x: x["value"], reverse=True)
        elif metric == "quantity":
            products.sort(key=lambda x: x["quantity"], reverse=True)
        elif metric == "fill_rate":
            products.sort(key=lambda x: x["fill_rate"], reverse=True)
        
        return products[:limit]


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    
    @staticmethod
    def welcome() -> str:
        return WELCOME_MESSAGE
    
    @staticmethod
    def format_snapshot_response(snapshot: AnalyticsSnapshot, dealer_rank: Dict) -> str:
        """Format analytics snapshot for WhatsApp display"""
        status_icon = DNStatusEngine.get_status_icon(
            DeliveryStatus(snapshot.delivery_status)
        )
        delay_icon = DNStatusEngine.get_delay_icon(
            DelayBucket(snapshot.delay_bucket)
        )
        
        return f"""╔══════════════════════════════════════════════════════════════════════════════╗
║                         📦 DN COMPLETE INTELLIGENCE REPORT                                 ║
║                                    {snapshot.dn_no}                                         ║
╚══════════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *DN SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Dealer: {snapshot.dealer}
   • City: {snapshot.city}
   • Warehouse: {snapshot.warehouse}
   • Status: {status_icon} {snapshot.delivery_status}
   • Delay: {delay_icon} {snapshot.delay_bucket}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 *PRODUCT SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Value: Rs {snapshot.total_value:,.2f}
   • Total Units: {snapshot.total_units:,.0f}
   • Product Count: {snapshot.product_count}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AGING ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Delivery Aging: {snapshot.delivery_aging} days
   • Pending Delivery Aging: {snapshot.pending_delivery_aging} days
   • POD Aging: {snapshot.pod_aging} days
   • Pending POD Aging: {snapshot.pending_pod_aging} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *HEALTH & RISK ASSESSMENT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • DN Health Score: {snapshot.dn_health_score}/100
   • Risk Score: {snapshot.risk_score}/100
   • Dealer Rank: #{dealer_rank.get('rank', 'N/A')}
   • Dealer Health Score: {dealer_rank.get('health_score', 0)}/100

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💬 *For assistance, type "Help" or ask any question*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    
    @staticmethod
    def format_product_dashboard(product_data: Dict) -> str:
        """Format product dashboard for WhatsApp"""
        if not product_data.get("success"):
            return product_data.get("message", "Product not found")
        
        fill_rate_icon = "🟢" if product_data["fill_rate"] >= 80 else "🟡" if product_data["fill_rate"] >= 50 else "🔴"
        
        response = f"""📦 *PRODUCT DASHBOARD: {product_data['product_name']}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *ORDER SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Ordered Qty: {product_data['ordered_qty']:,.0f}
• Delivered Qty: {product_data['delivered_qty']:,.0f} ✅
• Pending Qty: {product_data['pending_qty']:,.0f} ⏳
• Fill Rate: {fill_rate_icon} {product_data['fill_rate']}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *VALUE SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Ordered Value: Rs {product_data['ordered_value']:,.2f}
• Delivered Value: Rs {product_data['delivered_value']:,.2f}
• Pending Value: Rs {product_data['pending_value']:,.2f}
• Total DNs: {product_data['total_dns']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 *TOP DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for dealer, data in product_data.get("top_dealers", [])[:5]:
            response += f"• {dealer[:30]}: {data['quantity']:,.0f} units\n"
        
        return response
    
    @staticmethod
    def format_delay_analysis(snapshots: List[AnalyticsSnapshot], bucket: str) -> str:
        """Format delay analysis for specific bucket"""
        filtered = [s for s in snapshots if s.delay_bucket == bucket]
        
        if not filtered:
            return f"✅ No {bucket} delayed DNs found."
        
        icon_map = {
            "On Time": "🟢",
            "Minor Delay": "🟡",
            "Moderate Delay": "🟠",
            "Critical": "🔴",
            "Severe": "💀"
        }
        
        icon = icon_map.get(bucket, "📋")
        
        response = f"{icon} *{bucket.upper()} DELAYED DNs*\n\n"
        for s in filtered[:15]:
            response += f"🔢 *{s.dn_no}*\n"
            response += f"   🏪 {s.dealer[:30]}\n"
            response += f"   💰 Rs {s.total_value:,.2f}\n"
            response += f"   ⏱️ Pending: {s.pending_delivery_aging} days\n\n"
        
        return response
    
    @staticmethod
    def executive_summary_response(context: Dict) -> str:
        """Executive summary using pre-calculated analytics context"""
        pending = context.get("pending_metrics", {})
        pod = context.get("pod_metrics", {})
        revenue = context.get("revenue_metrics", {})
        dealers = context.get("dealer_rankings", [])[:5]
        
        response = f"""👑 *EXECUTIVE SUMMARY DASHBOARD*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *NETWORK HEALTH*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Pending DNs: {pending.get('pending_dns', 0)}
• Pending Value: Rs {pending.get('pending_value', 0):,.2f}
• Avg Pending Days: {pending.get('avg_pending_days', 0)} days
• Pending PODs: {pod.get('pending_pods', 0)}
• POD Pending Value: Rs {pod.get('pending_pod_value', 0):,.2f}
• Realization Rate: {revenue.get('realization_rate', 0)}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 *TOP 5 DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(dealers, 1):
            response += f"{i}. {d['name'][:30]} - Rs {d['total_value']:,.2f} (Health: {d['health_score']})\n"
        
        response += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRIORITY ACTIONS:*
1. Focus on pending POD collection
2. Review top delayed DNs
3. Follow up with low health score dealers

Type "Help" for all commands"""
        
        return response


# ==========================================================
# WELCOME MESSAGE
# ==========================================================

WELCOME_MESSAGE = """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT v18.0*

I provide real-time analytics on Dealers, DNs, Products, PODs, and more!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *What You Can Ask:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN Tracking* - Send any 6-15 digit number
🏪 *Dealers* - Type a dealer name
📦 *Products* - "Product HSU-18HFPAA" or "Top products"
🚨 *Delays* - "Severe delayed DNs", "Critical delayed"
📋 *POD Status* - "Pending PODs"
👑 *Executive Reports* - "Executive summary"
🏭 *Warehouses* - "Warehouse performance"
🌆 *Cities* - "City performance"
💰 *Financial* - "Revenue analysis"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Examples:*
"80012345" | "Exact Trading Co" | "Product HSU-18HFPAA" | "Severe delayed DNs" | "Executive summary" """


# ==========================================================
# CONVERSATION MEMORY
# ==========================================================

class ConversationMemory:
    def __init__(self, max_history: int = 10):
        self.history: Dict[str, deque] = {}
        self.max_history = max_history
    
    def get_or_create(self, phone_number: str) -> deque:
        if phone_number not in self.history:
            self.history[phone_number] = deque(maxlen=self.max_history)
        return self.history[phone_number]
    
    def add(self, phone_number: str, question: str, response: str, intent: str, entity: str = None):
        memory = self.get_or_create(phone_number)
        memory.append({
            "question": question,
            "response": response[:200],
            "intent": intent,
            "entity": entity,
            "timestamp": datetime.utcnow().isoformat()
        })
    
    def get_last_context(self, phone_number: str) -> Dict[str, Any]:
        memory = self.get_or_create(phone_number)
        if not memory:
            return {}
        last = memory[-1]
        return {
            "last_question": last.get("question"),
            "last_intent": last.get("intent"),
            "last_entity": last.get("entity"),
            "recent_entities": [m.get("entity") for m in list(memory)[-3:] if m.get("entity")]
        }


# ==========================================================
# INTENT TYPES & DETECTION
# ==========================================================

class IntentType(str, Enum):
    HELP = "help"
    WELCOME = "welcome"
    DEALER_LOOKUP = "dealer_lookup"
    DN_LOOKUP = "dn_lookup"
    TOP_DEALERS = "top_dealers"
    TOP_RISK_DEALERS = "top_risk_dealers"
    EXECUTIVE_SUMMARY = "executive_summary"
    NETWORK_HEALTH = "network_health"
    CITY_PERFORMANCE = "city_performance"
    WAREHOUSE_PERFORMANCE = "warehouse_performance"
    REVENUE_ANALYSIS = "revenue_analysis"
    OUTSTANDING_ANALYSIS = "outstanding_analysis"
    POD_ANALYSIS = "pod_analysis"
    PENDING_POD = "pending_pod"
    PRODUCT_ANALYSIS = "product_analysis"
    PRODUCT_RANKING = "product_ranking"
    DELAY_ANALYSIS = "delay_analysis"
    GENERAL_QUERY = "general_query"


class IntentDetector:
    
    @staticmethod
    def is_numeric_dn(message: str) -> Tuple[bool, Optional[str]]:
        cleaned = message.strip()
        cleaned = re.sub(r'[\s\-]', '', cleaned)
        
        if cleaned.isdigit() and 6 <= len(cleaned) <= 15:
            return True, cleaned
        return False, None
    
    @staticmethod
    def detect_intent(message: str) -> Tuple[IntentType, Optional[str]]:
        msg_lower = message.lower().strip()
        msg_original = message.strip()
        
        # DN number detection (highest priority)
        is_numeric, numeric_dn = IntentDetector.is_numeric_dn(msg_original)
        if is_numeric:
            return IntentType.DN_LOOKUP, numeric_dn
        
        # Help/Welcome
        if any(word in msg_lower for word in ["help", "menu", "welcome", "hello", "hi", "hey"]):
            return IntentType.HELP, None
        
        # Delay analysis (Priority 5)
        if any(word in msg_lower for word in ["severe delayed", "severe delay"]):
            return IntentType.DELAY_ANALYSIS, "Severe"
        if any(word in msg_lower for word in ["critical delayed", "critical delay"]):
            return IntentType.DELAY_ANALYSIS, "Critical"
        if any(word in msg_lower for word in ["moderate delayed", "moderate delay"]):
            return IntentType.DELAY_ANALYSIS, "Moderate Delay"
        if any(word in msg_lower for word in ["minor delayed", "minor delay"]):
            return IntentType.DELAY_ANALYSIS, "Minor Delay"
        if "on time" in msg_lower:
            return IntentType.DELAY_ANALYSIS, "On Time"
        
        # Product queries (Priority 9)
        if any(word in msg_lower for word in ["product", "model", "sku"]):
            if "top" in msg_lower:
                return IntentType.PRODUCT_RANKING, "top"
            product_match = re.search(r'([A-Z]{2,3}-[0-9A-Z]+)', msg_original.upper())
            if product_match:
                return IntentType.PRODUCT_ANALYSIS, product_match.group(1)
            return IntentType.PRODUCT_RANKING, "top"
        
        # Executive queries
        if any(word in msg_lower for word in ["executive summary", "ceo summary", "management summary"]):
            return IntentType.EXECUTIVE_SUMMARY, None
        
        if any(word in msg_lower for word in ["network health", "health score", "network status"]):
            return IntentType.NETWORK_HEALTH, None
        
        # Dealer queries
        if any(word in msg_lower for word in ["top dealer", "top performing", "best dealer"]):
            return IntentType.TOP_DEALERS, None
        
        if "risk dealer" in msg_lower:
            return IntentType.TOP_RISK_DEALERS, None
        
        # POD queries
        if any(word in msg_lower for word in ["pending pod", "pod pending"]):
            return IntentType.PENDING_POD, None
        
        # Performance queries
        if any(word in msg_lower for word in ["city performance", "city wise"]):
            return IntentType.CITY_PERFORMANCE, None
        
        if any(word in msg_lower for word in ["warehouse performance", "warehouse wise"]):
            return IntentType.WAREHOUSE_PERFORMANCE, None
        
        # Financial queries
        if any(word in msg_lower for word in ["revenue analysis", "revenue summary"]):
            return IntentType.REVENUE_ANALYSIS, None
        
        if any(word in msg_lower for word in ["outstanding", "pending value"]):
            return IntentType.OUTSTANDING_ANALYSIS, None
        
        # Default to dealer lookup for short text
        if len(msg_lower.split()) <= 5 and not msg_lower.isdigit():
            return IntentType.DEALER_LOOKUP, msg_original
        
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# MAIN AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    
    def __init__(self, db: Session):
        self.db = db
        self.analytics_context = AnalyticsContext(db)
        self.dealer_cache = DealerCache(db)
        self.dn_cache = DNCache()
        self.product_engine = ProductIntelligenceEngine(db, self.analytics_context)
        self.formatter = ResponseFormatter()
        self.conversation_memory = ConversationMemory()
        
        # Pre-load dealer cache at startup (Priority 8)
        self.dealer_cache.load()
        
        # Initialize analytics context
        self.analytics_context.refresh()
        
        logger.info("=" * 50)
        logger.info("🚀 AI LOGISTICS INTELLIGENCE ASSISTANT v18.0 (ANALYTICS SNAPSHOT)")
        logger.info("=" * 50)
    
    def process_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        start_time = time.time()
        question = question.strip()
        
        logger.info(f"📱 Processing: {question[:100]}")
        
        conversation_context = {}
        if user_phone:
            conversation_context = self.conversation_memory.get_last_context(user_phone)
        
        intent, entity = IntentDetector.detect_intent(question)
        logger.info(f"🎯 Intent: {intent.value}, Entity: {entity}")
        
        try:
            if intent == IntentType.HELP or intent == IntentType.WELCOME:
                result = self._handle_welcome()
            elif intent == IntentType.DEALER_LOOKUP:
                result = self._handle_dealer_lookup(entity)
            elif intent == IntentType.DN_LOOKUP:
                result = self._handle_dn_lookup(entity)
            elif intent == IntentType.TOP_DEALERS:
                result = self._handle_top_dealers()
            elif intent == IntentType.TOP_RISK_DEALERS:
                result = self._handle_top_risk_dealers()
            elif intent == IntentType.EXECUTIVE_SUMMARY:
                result = self._handle_executive_summary()
            elif intent == IntentType.NETWORK_HEALTH:
                result = self._handle_network_health()
            elif intent == IntentType.CITY_PERFORMANCE:
                result = self._handle_city_performance()
            elif intent == IntentType.WAREHOUSE_PERFORMANCE:
                result = self._handle_warehouse_performance()
            elif intent == IntentType.REVENUE_ANALYSIS:
                result = self._handle_revenue_analysis()
            elif intent == IntentType.OUTSTANDING_ANALYSIS:
                result = self._handle_outstanding_analysis()
            elif intent == IntentType.PENDING_POD:
                result = self._handle_pending_pods()
            elif intent == IntentType.PRODUCT_ANALYSIS:
                result = self._handle_product_analysis(entity)
            elif intent == IntentType.PRODUCT_RANKING:
                result = self._handle_product_ranking(entity)
            elif intent == IntentType.DELAY_ANALYSIS:
                result = self._handle_delay_analysis(entity)
            else:
                result = self._handle_general_query(question, conversation_context)
            
            if user_phone and result.get("success"):
                self.conversation_memory.add(user_phone, question, result.get("response", ""), intent.value, entity)
            
            result["processing_time_ms"] = int((time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            logger.error(f"Processing error: {e}")
            return {"success": False, "response": "⚠️ Service unavailable. Please try again.", "processing_time_ms": int((time.time() - start_time) * 1000)}
    
    def _handle_welcome(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.welcome()}
    
    def _handle_dealer_lookup(self, dealer_name: str) -> Dict[str, Any]:
        """Fast dealer lookup using dealer cache"""
        if not dealer_name:
            return {"success": False, "response": "🏪 Please provide a dealer name."}
        
        # Search using dealer cache
        matches = self.dealer_cache.search(dealer_name, limit=5)
        
        if not matches:
            return {"success": False, "response": f"🏪 Dealer '{dealer_name}' not found."}
        
        if len(matches) == 1:
            dealer = matches[0]
            dealer_data = self.analytics_context.get_dealer_rank(dealer)
            
            response = f"""🏪 *DEALER: {dealer}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Rank: #{dealer_data.get('rank', 'N/A')}
• Health Score: {dealer_data.get('health_score', 0)}/100
• Total Value: Rs {dealer_data.get('total_value', 0):,.2f}
• Total DNs: {dealer_data.get('total_dns', 0)}
• Completion Rate: {dealer_data.get('completion_rate', 0)}%
• POD Rate: {dealer_data.get('pod_rate', 0)}%

💡 Type "Executive summary" for overall network health"""
            
            return {"success": True, "response": response}
        else:
            suggestions = "\n".join([f"• {m}" for m in matches])
            return {"success": False, "response": f"🏪 Multiple dealers found:\n\n{suggestions}\n\nPlease be more specific."}
    
    def _handle_dn_lookup(self, dn_number: str) -> Dict[str, Any]:
        """DN lookup using cache (Priority 7)"""
        # Check cache first
        cached = self.dn_cache.get(dn_number)
        if cached:
            logger.info(f"✅ DN {dn_number} served from cache")
            return cached
        
        # Get snapshot from analytics context
        snapshot = self.analytics_context.get_snapshot(dn_number)
        
        if not snapshot:
            # Fallback to direct DB query
            record = self.db.query(DeliveryReport).filter(DeliveryReport.dn_no == dn_number).first()
            if not record:
                return {"success": False, "response": f"❌ DN {dn_number} not found"}
            
            # Create snapshot on the fly
            dealer_rank = self.analytics_context.get_dealer_rank(record.customer_name or "")
            warehouse_score = self.analytics_context.get_warehouse_score(record.warehouse or "")
            
            response = self._build_dn_response_from_record(record, dealer_rank, warehouse_score)
        else:
            dealer_rank = self.analytics_context.get_dealer_rank(snapshot.dealer)
            response = self.formatter.format_snapshot_response(snapshot, dealer_rank)
        
        result = {"success": True, "response": response}
        
        # Cache the result (Priority 7)
        self.dn_cache.set(dn_number, result)
        
        return result
    
    def _build_dn_response_from_record(self, record, dealer_rank: Dict, warehouse_score: int) -> str:
        """Fallback response builder when snapshot not available"""
        status = DNStatusEngine.get_status(record.pgi_status, record.pod_status)
        status_icon = DNStatusEngine.get_status_icon(status)
        
        return f"""╔══════════════════════════════════════════════════════════════════════════════╗
║                         📦 DN COMPLETE INTELLIGENCE REPORT                                 ║
║                                    {record.dn_no}                                          ║
╚══════════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *DN SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Dealer: {record.customer_name}
   • City: {record.ship_to_city or 'N/A'}
   • Warehouse: {record.warehouse or 'N/A'}
   • Status: {status_icon} {status.value}
   • DN Date: {record.dn_create_date.strftime('%d-%b-%Y') if record.dn_create_date else 'N/A'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 *PRODUCT SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Product: {record.product or 'N/A'}
   • Quantity: {float(record.dn_qty or 0):,.0f}
   • Value: Rs {float(record.dn_amount or 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *HEALTH & RISK ASSESSMENT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Dealer Rank: #{dealer_rank.get('rank', 'N/A')}
   • Dealer Health Score: {dealer_rank.get('health_score', 0)}/100

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💬 *For assistance, type "Help" or ask any question*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    
    def _handle_top_dealers(self) -> Dict[str, Any]:
        context = self.analytics_context.get()
        dealers = context.get("dealer_rankings", [])[:20]
        
        if not dealers:
            return {"success": True, "response": "🏪 No dealer data available."}
        
        response = "🏆 *TOP 20 PERFORMING DEALERS*\n\n"
        for i, d in enumerate(dealers, 1):
            health_icon = "🟢" if d["health_score"] >= 70 else "🟡" if d["health_score"] >= 50 else "🔴"
            response += f"{i}. *{d['name'][:35]}*\n"
            response += f"   💰 Rs {d['total_value']:,.2f} | 📦 {d['total_dns']} DNs\n"
            response += f"   {health_icon} Health: {d['health_score']}%\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_top_risk_dealers(self) -> Dict[str, Any]:
        context = self.analytics_context.get()
        dealers = context.get("dealer_rankings", [])
        
        # Filter low health score dealers
        risk_dealers = [d for d in dealers if d["health_score"] < 50]
        risk_dealers.sort(key=lambda x: x["health_score"])
        
        if not risk_dealers:
            return {"success": True, "response": "✅ No high-risk dealers found."}
        
        response = "🚨 *TOP RISK DEALERS*\n\n"
        for i, d in enumerate(risk_dealers[:20], 1):
            risk_icon = "🔴" if d["health_score"] < 30 else "🟡"
            response += f"{i}. *{d['name'][:35]}*\n"
            response += f"   {risk_icon} Health Score: {d['health_score']}%\n"
            response += f"   📦 {d['total_dns']} DNs | {d['completion_rate']}% delivered\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_executive_summary(self) -> Dict[str, Any]:
        context = self.analytics_context.get()
        response = self.formatter.executive_summary_response(context)
        return {"success": True, "response": response}
    
    def _handle_network_health(self) -> Dict[str, Any]:
        context = self.analytics_context.get()
        pending = context.get("pending_metrics", {})
        pod = context.get("pod_metrics", {})
        revenue = context.get("revenue_metrics", {})
        
        response = f"""📊 *NETWORK HEALTH SCORE*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {context.get('total_dns', 'N/A')}
• Pending DNs: {pending.get('pending_dns', 0)}
• Pending Value: Rs {pending.get('pending_value', 0):,.2f}
• Avg Pending Days: {pending.get('avg_pending_days', 0)} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *POD METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Pending PODs: {pod.get('pending_pods', 0)}
• POD Pending Value: Rs {pod.get('pending_pod_value', 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *REVENUE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Revenue: Rs {revenue.get('total_revenue', 0):,.2f}
• Realized Revenue: Rs {revenue.get('realized_revenue', 0):,.2f}
• Realization Rate: {revenue.get('realization_rate', 0)}%

💡 Type "Executive summary" for detailed analysis"""
        
        return {"success": True, "response": response}
    
    def _handle_city_performance(self) -> Dict[str, Any]:
        context = self.analytics_context.get()
        cities = context.get("city_rankings", [])[:15]
        
        if not cities:
            return {"success": True, "response": "🌆 No city data available."}
        
        response = "🌆 *CITY PERFORMANCE*\n\n"
        for c in cities:
            completion_icon = "🟢" if c["completion_rate"] >= 80 else "🟡" if c["completion_rate"] >= 50 else "🔴"
            response += f"{completion_icon} *{c['name'][:25]}*\n"
            response += f"   📦 {c['total_dns']} DNs | {c['completion_rate']:.0f}% completed\n"
            response += f"   💰 Rs {c['total_value']:,.2f}\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_warehouse_performance(self) -> Dict[str, Any]:
        context = self.analytics_context.get()
        warehouses = context.get("warehouse_rankings", [])[:15]
        
        if not warehouses:
            return {"success": True, "response": "🏭 No warehouse data available."}
        
        response = "🏭 *WAREHOUSE PERFORMANCE*\n\n"
        for w in warehouses:
            completion_icon = "🟢" if w["completion_rate"] >= 80 else "🟡" if w["completion_rate"] >= 50 else "🔴"
            response += f"{completion_icon} *{w['name'][:25]}*\n"
            response += f"   📦 {w['total_dns']} DNs | {w['completion_rate']:.0f}% completed\n"
            response += f"   💰 Rs {w['total_value']:,.2f}\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_revenue_analysis(self) -> Dict[str, Any]:
        context = self.analytics_context.get()
        revenue = context.get("revenue_metrics", {})
        
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
    
    def _handle_outstanding_analysis(self) -> Dict[str, Any]:
        context = self.analytics_context.get()
        pending = context.get("pending_metrics", {})
        pod = context.get("pod_metrics", {})
        
        outstanding = pending.get("pending_value", 0) + pod.get("pending_pod_value", 0)
        
        response = f"""💰 *OUTSTANDING & PENDING VALUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *VALUE BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Outstanding: Rs {outstanding:,.2f}
• Pending Delivery: Rs {pending.get('pending_value', 0):,.2f} ⏳
• POD Pending: Rs {pod.get('pending_pod_value', 0):,.2f} 📋"""
        
        return {"success": True, "response": response}
    
    def _handle_pending_pods(self) -> Dict[str, Any]:
        context = self.analytics_context.get()
        pod = context.get("pod_metrics", {})
        
        # Get detailed pending PODs from snapshots
        snapshots = context.get("snapshots", {})
        pending_pod_snapshots = [
            s for s in snapshots.values() 
            if s.pending_pod_aging > 0
        ]
        pending_pod_snapshots.sort(key=lambda x: x.pending_pod_aging, reverse=True)
        
        if not pending_pod_snapshots:
            return {"success": True, "response": "✅ No pending PODs found."}
        
        response = f"📋 *PENDING PODs ({len(pending_pod_snapshots)})*\n\n"
        for s in pending_pod_snapshots[:15]:
            response += f"🔢 *{s.dn_no}*\n"
            response += f"   🏪 {s.dealer[:30]}\n"
            response += f"   💰 Rs {s.total_value:,.2f}\n"
            response += f"   ⏱️ {s.pending_pod_aging} days pending\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_product_analysis(self, product_name: str) -> Dict[str, Any]:
        if not product_name:
            return {"success": True, "response": "📦 Please specify a product model (e.g., HSU-18HFPAA)"}
        
        result = self.product_engine.get_product_dashboard(product_name)
        if result.get("success"):
            response = self.formatter.format_product_dashboard(result)
        else:
            response = result.get("message", "Product not found")
        
        return {"success": result.get("success", False), "response": response}
    
    def _handle_product_ranking(self, ranking_type: str) -> Dict[str, Any]:
        if ranking_type == "top":
            products = self.product_engine.get_product_ranking("value", 10)
            if not products:
                return {"success": True, "response": "📦 No product data available."}
            
            response = "🏆 *TOP 10 PRODUCTS (by Revenue)*\n\n"
            for i, p in enumerate(products, 1):
                fill_icon = "🟢" if p["fill_rate"] >= 80 else "🟡" if p["fill_rate"] >= 50 else "🔴"
                response += f"{i}. *{p['name'][:35]}*\n"
                response += f"   💰 Rs {p['value']:,.2f}\n"
                response += f"   📦 {p['quantity']:,.0f} units | Fill: {fill_icon} {p['fill_rate']}%\n\n"
        else:
            products = self.product_engine.get_product_ranking("fill_rate", 10)
            response = "📦 *PRODUCTS BY FILL RATE*\n\n"
            for i, p in enumerate(products, 1):
                response += f"{i}. *{p['name'][:35]}*\n"
                response += f"   Fill Rate: {p['fill_rate']}%\n"
                response += f"   📦 Delivered: {p['quantity']:,.0f} units\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_delay_analysis(self, bucket: str) -> Dict[str, Any]:
        """Handle delay bucket queries (Priority 5)"""
        context = self.analytics_context.get()
        snapshots = list(context.get("snapshots", {}).values())
        
        response = self.formatter.format_delay_analysis(snapshots, bucket)
        return {"success": True, "response": response}
    
    def _handle_general_query(self, question: str, conversation_context: Dict = None) -> Dict[str, Any]:
        """Handle general queries using analytics context"""
        
        # Check if asking about delays
        if "delay" in question.lower():
            if "severe" in question.lower():
                return self._handle_delay_analysis("Severe")
            elif "critical" in question.lower():
                return self._handle_delay_analysis("Critical")
            elif "moderate" in question.lower():
                return self._handle_delay_analysis("Moderate Delay")
            elif "minor" in question.lower():
                return self._handle_delay_analysis("Minor Delay")
        
        # Check if asking about revenue
        if any(word in question.lower() for word in ["revenue", "sales", "value"]):
            return self._handle_revenue_analysis()
        
        # Default response with available commands
        response = f"""🤖 *AI LOGISTICS ASSISTANT v18.0*

I understand you're asking about: "{question[:50]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Available commands:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 Send a DN number (6-15 digits)
🏪 Type a dealer name
📦 "Top products" or "Product HSU-18HFPAA"
🚨 "Severe delayed DNs" or "Critical delayed"
👑 "Executive summary"
📋 "Pending PODs"
💰 "Revenue analysis"

Type "Help" for complete menu."""
        
        return {"success": True, "response": response}


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def process_whatsapp_query(question: str, db: Session, user_phone: str = None) -> str:
    """Process WhatsApp query and return response"""
    try:
        service = AIQueryService(db)
        result = service.process_query(question, user_phone)
        return result.get("response", "Unable to process your request. Please try again.")
    except Exception as e:
        logger.error(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


# ==========================================================
# BACKGROUND REFRESHER (Optional - for production)
# ==========================================================

class AnalyticsRefresher:
    """Background refresher for analytics context"""
    
    def __init__(self, db_session_factory):
        self.db_session_factory = db_session_factory
        self._running = False
    
    async def start(self):
        """Start background refresh task"""
        self._running = True
        while self._running:
            try:
                with self.db_session_factory() as db:
                    context = AnalyticsContext(db)
                    context.refresh()
                await asyncio.sleep(900)  # 15 minutes
            except Exception as e:
                logger.error(f"Analytics refresh error: {e}")
                await asyncio.sleep(60)
    
    def stop(self):
        self._running = False
