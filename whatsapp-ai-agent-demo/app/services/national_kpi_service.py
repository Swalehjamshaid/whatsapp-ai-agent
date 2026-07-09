#!/usr/bin/env python3
# ============================================================
# FILE: app/services/national_kpi_service.py
# VERSION: 6.1 - INTEGRATED WITH AI PROVIDER
# PURPOSE: National executive dashboard and logistics intelligence
# ============================================================

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional, Dict, List, Tuple
from functools import lru_cache

from cachetools import TTLCache
from sqlalchemy import and_, case, distinct, func, or_, text, desc, asc
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import DeliveryReport

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: CONFIGURATION
# ============================================================

CACHE_TTL = max(60, int(os.getenv("NATIONAL_KPI_CACHE_TTL", "300")))
VERSION = "6.1"

# ============================================================
# BLOCK 2: UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    try:
        return str(value).strip() or default
    except (TypeError, ValueError):
        return default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 1) if bottom else 0.0

def _format_currency(amount: float) -> str:
    if amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.1f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _format_number(num: int) -> str:
    return f"{num:,}"

def _days(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "days"):
        return round(float(value.days), 2)
    return round(_number(value), 2)

def _get_current_month() -> str:
    """Get current month name"""
    return datetime.now().strftime("%B %Y")

# ============================================================
# BLOCK 3: NATIONAL KPI SERVICE
# ============================================================

class NationalKPIService:
    """
    National Logistics Intelligence Engine
    PostgreSQL is the ONLY source of truth.
    """
    
    def __init__(self) -> None:
        self._version = VERSION
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=100, ttl=CACHE_TTL)
        logger.info(f"✅ NationalKPIService v{self._version} initialized")
    
    def handle_message(self, message: str, sender: str) -> str:
        """Main entry point - returns KPI dashboard"""
        try:
            message_clean = message.strip()
            
            # Check if it's 99
            if message_clean == '99':
                return self._get_help_message()
            
            # Check if it's a greeting or empty
            if not message_clean or message_clean.lower() in ['hi', 'hello', 'hey', 'start', 'menu']:
                return self._get_welcome_message()
            
            # Check for specific commands
            if message_clean.lower() in ['dashboard', 'kpi', 'national', 'warehouse kpi']:
                return self.get_warehouse_kpi_dashboard()
            
            # Check for warehouse specific
            warehouse = self._resolve_warehouse_name(message_clean)
            if warehouse:
                return self.get_warehouse_kpi_dashboard(warehouse)
            
            return self.get_warehouse_kpi_dashboard()
            
        except Exception as e:
            logger.exception(f"Error in handle_message: {e}")
            return f"⚠️ Error: {str(e)}\n\nPlease try again."
    
    def _get_welcome_message(self) -> str:
        """Get welcome message"""
        return """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏬 NATIONAL LOGISTICS INTELLIGENCE CENTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Welcome to the National Logistics Intelligence Platform!

🔍 **How to use:**
• Type 'dashboard' or 'kpi' for national KPI dashboard
• Type any warehouse name for specific warehouse KPI
• Examples:
  - dashboard
  - Lahore
  - Karachi

📊 **What you'll see:**
• Warehouse KPI Performance dashboard
• POD, PGI, Delivery, and Cycle times
• National averages
• AI-powered insights

💡 **Pro tip:** 
Type **99** for quick help anytime!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type a command to get started!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    
    def _get_help_message(self) -> str:
        """Get help message"""
        return """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 QUICK HELP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This is the National Logistics Intelligence system.

🔍 **Commands:**
• 'dashboard' or 'kpi' - Show national KPI dashboard
• 'Lahore' - Show Lahore warehouse KPI
• 'Karachi' - Show Karachi warehouse KPI
• '99' - Return to main menu

📊 **What you'll see:**
• Warehouse KPI Performance dashboard
• POD, PGI, Delivery, and Cycle times
• National averages
• AI-powered insights

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type a command to get started!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
    
    def _resolve_warehouse_name(self, name: str) -> Optional[str]:
        """Resolve warehouse name from database"""
        if not name or not name.strip():
            return None
        
        name_normalized = name.strip().lower()
        
        try:
            with engine.connect() as conn:
                # Exact match
                result = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(warehouse) as warehouse
                        FROM delivery_reports 
                        WHERE LOWER(TRIM(warehouse)) = LOWER(:name)
                        AND warehouse IS NOT NULL
                        AND TRIM(warehouse) != ''
                        LIMIT 1
                    """),
                    {"name": name_normalized}
                ).first()
                
                if result:
                    return result[0]
                
                # ILIKE match
                result = conn.execute(
                    text("""
                        SELECT DISTINCT TRIM(warehouse) as warehouse
                        FROM delivery_reports 
                        WHERE TRIM(warehouse) ILIKE :pattern
                        AND warehouse IS NOT NULL
                        AND TRIM(warehouse) != ''
                        LIMIT 1
                    """),
                    {"pattern": f"%{name}%"}
                ).first()
                
                if result:
                    return result[0]
                
        except Exception as e:
            logger.exception(f"Error resolving warehouse name: {e}")
        
        return None
    
    def get_warehouse_kpi_dashboard(self, specific_warehouse: Optional[str] = None) -> str:
        """
        Get the warehouse KPI performance dashboard.
        Shows POD, PGI, Delivery Days, and Cycle Time for all warehouses.
        """
        try:
            cache_key = f"kpi_dashboard_{specific_warehouse or 'all'}"
            
            # Check cache
            if cache_key in self._cache:
                return self._cache[cache_key]
            
            with engine.connect() as conn:
                # Base query for warehouse KPI data
                query = """
                    SELECT 
                        TRIM(warehouse) as warehouse,
                        COUNT(DISTINCT dn_no) as total_dn,
                        COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as pod_completed,
                        COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) as pgi_completed,
                        AVG(CASE WHEN good_issue_date IS NOT NULL 
                            THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400 
                            ELSE NULL END) as avg_delivery_days,
                        AVG(CASE WHEN pod_date IS NOT NULL AND good_issue_date IS NOT NULL 
                            THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date)) / 86400 
                            ELSE NULL END) as avg_pod_days,
                        AVG(CASE WHEN pod_date IS NOT NULL 
                            THEN EXTRACT(EPOCH FROM (pod_date - dn_create_date)) / 86400 
                            ELSE NULL END) as avg_cycle_days
                    FROM delivery_reports 
                    WHERE warehouse IS NOT NULL 
                    AND TRIM(warehouse) != ''
                """
                
                # Add warehouse filter if specific warehouse requested
                if specific_warehouse:
                    query += f" AND LOWER(TRIM(warehouse)) = LOWER('{specific_warehouse}')"
                
                query += """
                    GROUP BY warehouse
                    ORDER BY warehouse
                """
                
                results = conn.execute(text(query)).fetchall()
                
                if not results:
                    return "⚠️ No warehouse data found."
                
                # Get national averages
                national_avg = self._get_national_averages()
                
                # Build the dashboard
                lines = [
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "🏬 WAREHOUSE KPI PERFORMANCE",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                    f"📅 Period: {_get_current_month()}",
                    "",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "Warehouse        POD   PGI  Delivery  Cycle",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                ]
                
                # Process each warehouse
                warehouse_data = []
                for row in results:
                    warehouse = _text(row[0])
                    total_dn = int(row[1] or 0)
                    pod_completed = int(row[2] or 0)
                    pgi_completed = int(row[3] or 0)
                    avg_delivery = float(row[4] or 0)
                    avg_pod = float(row[5] or 0)
                    avg_cycle = float(row[6] or 0)
                    
                    pod_pct = _percent(pod_completed, total_dn)
                    pgi_pct = _percent(pgi_completed, total_dn)
                    
                    warehouse_data.append({
                        'warehouse': warehouse,
                        'pod_pct': pod_pct,
                        'pgi_pct': pgi_pct,
                        'delivery_days': avg_delivery,
                        'cycle_days': avg_cycle,
                    })
                
                # Sort by POD percentage (highest first)
                warehouse_data.sort(key=lambda x: x['pod_pct'], reverse=True)
                
                # Render each warehouse
                for data in warehouse_data:
                    warehouse = data['warehouse']
                    pod_pct = data['pod_pct']
                    pgi_pct = data['pgi_pct']
                    delivery_days = data['delivery_days']
                    cycle_days = data['cycle_days']
                    
                    # Format the row with proper spacing
                    lines.append(f"🏬 {warehouse:<14} {pod_pct:>5.1f}% {pgi_pct:>5.1f}% {delivery_days:>5.1f}D {cycle_days:>6.1f}D")
                
                lines.extend([
                    "",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "📊 NATIONAL AVERAGE",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                    f"📄 POD            {national_avg['pod']:.1f}%",
                    f"⚡ PGI            {national_avg['pgi']:.1f}%",
                    f"🚚 Delivery Days  {national_avg['delivery']:.1f}",
                    f"⏱️ Total Cycle    {national_avg['cycle']:.1f} Days",
                    "",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "🤖 AI INSIGHTS",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                ])
                
                # Add AI insights
                insights = self._generate_insights(warehouse_data, national_avg)
                for insight in insights:
                    lines.append(insight)
                
                lines.extend([
                    "",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                    "Type a warehouse name to search",
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                ])
                
                response = "\n".join(lines)
                
                # Cache the response
                self._cache[cache_key] = response
                
                return response
                
        except Exception as e:
            logger.exception(f"Error getting warehouse KPI dashboard: {e}")
            return f"⚠️ Error loading KPI dashboard: {str(e)}"
    
    def _get_national_averages(self) -> Dict[str, float]:
        """Get national averages for KPI metrics"""
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT 
                            COUNT(DISTINCT dn_no) as total_dn,
                            COUNT(DISTINCT CASE WHEN pod_date IS NOT NULL THEN dn_no END) as pod_completed,
                            COUNT(DISTINCT CASE WHEN good_issue_date IS NOT NULL THEN dn_no END) as pgi_completed,
                            AVG(CASE WHEN good_issue_date IS NOT NULL 
                                THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400 
                                ELSE NULL END) as avg_delivery,
                            AVG(CASE WHEN pod_date IS NOT NULL AND good_issue_date IS NOT NULL 
                                THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date)) / 86400 
                                ELSE NULL END) as avg_pod,
                            AVG(CASE WHEN pod_date IS NOT NULL 
                                THEN EXTRACT(EPOCH FROM (pod_date - dn_create_date)) / 86400 
                                ELSE NULL END) as avg_cycle
                        FROM delivery_reports 
                        WHERE warehouse IS NOT NULL 
                        AND TRIM(warehouse) != ''
                    """)
                ).first()
                
                if result:
                    total_dn = int(result[0] or 0)
                    pod_completed = int(result[1] or 0)
                    pgi_completed = int(result[2] or 0)
                    
                    return {
                        'pod': _percent(pod_completed, total_dn),
                        'pgi': _percent(pgi_completed, total_dn),
                        'delivery': float(result[3] or 0),
                        'pod_days': float(result[4] or 0),
                        'cycle': float(result[5] or 0),
                    }
                
                return {'pod': 0, 'pgi': 0, 'delivery': 0, 'pod_days': 0, 'cycle': 0}
                
        except Exception as e:
            logger.error(f"Error getting national averages: {e}")
            return {'pod': 0, 'pgi': 0, 'delivery': 0, 'pod_days': 0, 'cycle': 0}
    
    def _generate_insights(self, warehouse_data: List[Dict], national_avg: Dict) -> List[str]:
        """Generate AI insights from warehouse data"""
        insights = []
        
        if not warehouse_data:
            insights.append("📊 No warehouse data available for insights.")
            return insights
        
        # Find best and worst POD
        best_pod = max(warehouse_data, key=lambda x: x['pod_pct'])
        worst_pod = min(warehouse_data, key=lambda x: x['pod_pct'])
        
        insights.append(f"🟢 Best POD       {best_pod['warehouse']} ({best_pod['pod_pct']:.1f}%)")
        insights.append(f"🔴 Lowest POD     {worst_pod['warehouse']} ({worst_pod['pod_pct']:.1f}%)")
        insights.append("")
        
        # PGI analysis
        if national_avg['pgi'] >= 98:
            insights.append("⚡ PGI exceeded the 98% target across all warehouses.")
        else:
            insights.append(f"⚡ PGI at {national_avg['pgi']:.1f}% - {'Meets' if national_avg['pgi'] >= 95 else 'Below'} target.")
        
        # Identify warehouses needing improvement
        low_performers = [w for w in warehouse_data if w['pod_pct'] < 85]
        if low_performers:
            warehouses = [w['warehouse'] for w in low_performers[:7]]
            if len(warehouses) > 1:
                insights.append(f"⚠️ {', '.join(warehouses[:-1])} and {warehouses[-1]} require focused action to improve POD compliance.")
            else:
                insights.append(f"⚠️ {warehouses[0]} requires focused action to improve POD compliance.")
        else:
            insights.append("✅ All warehouses are meeting POD targets.")
        
        # Additional insights
        if national_avg['delivery'] > 2.5:
            insights.append(f"🚚 Average delivery time of {national_avg['delivery']:.1f} days - consider optimization.")
        
        return insights
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for service"""
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM delivery_reports")).first()
                rows = int(result[0] or 0)
                
            return {
                "healthy": True,
                "service": "national_kpi",
                "version": self._version,
                "database": "connected",
                "records": rows,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "PostgreSQL",
            }
        except Exception as e:
            return {
                "healthy": False,
                "service": "national_kpi",
                "version": self._version,
                "database": "disconnected",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat(),
            }

# ============================================================
# BLOCK 4: SINGLETON & EXPORTS
# ============================================================

_national_service: Optional[NationalKPIService] = None

def get_national_kpi_service() -> NationalKPIService:
    global _national_service
    if _national_service is None:
        logger.info("🔧 Creating NationalKPIService instance...")
        _national_service = NationalKPIService()
        logger.info("✅ NationalKPIService instance created successfully")
    return _national_service

# ============================================================
# 🆕 ALIAS FOR BACKWARD COMPATIBILITY WITH AI PROVIDER
# ============================================================
def get_kpi_service() -> NationalKPIService:
    """
    Alias for get_national_kpi_service().
    This is used by ai_provider_service.py for backward compatibility.
    DO NOT REMOVE - Required for ai_provider_service.py integration.
    """
    return get_national_kpi_service()

def get_national_kpi_dashboard() -> str:
    """Get the national KPI dashboard"""
    service = get_national_kpi_service()
    return service.get_warehouse_kpi_dashboard()

__all__ = [
    "NationalKPIService",
    "get_national_kpi_service",
    "get_kpi_service",  # ✅ Required for ai_provider_service.py
    "get_national_kpi_dashboard",
    "VERSION",
]
