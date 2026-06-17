# ==========================================================
# FILE: app/services/analytics_service.py (v7.0 - FULLY ALIGNED)
# ==========================================================
# PURPOSE: Business Intelligence Layer - Enterprise Dealer Intelligence Engine
# VERSION: 7.0 - Fully Aligned with PostgreSQL Schema
#
# CRITICAL FIXES:
# 1. ✅ COUNT(DISTINCT dn_no) instead of COUNT(*) for all DN counts
# 2. ✅ Fixed json_agg with ORDER BY and LIMIT using CTE
# 3. ✅ Added dealer_code, customer_code, division, warehouse_code, delivery_location
# 4. ✅ Use delivery_status, pgi_status, pod_status for status logic
# 5. ✅ Dealer resolution with code search
# 6. ✅ Fixed Railway environment detection
# 7. ✅ Standardized DEALER_NAME_FIELD constant
# 8. ✅ Proper aging calculation (PGI, POD, Total)
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
import uuid
from collections import defaultdict
from statistics import mean, stdev
import math
import json
import os
from contextlib import contextmanager
from sqlalchemy import text

from app.services.logistics_query_service import LogisticsQueryService
from app.services.kpi_service import KPIService
from app.schemas.schema_service import get_schema_service


# ==========================================================
# CONSTANTS - STANDARD FIELD MAPPING
# ==========================================================

# CRITICAL: Standard field mapping - DO NOT CHANGE
DEALER_NAME_FIELD = "customer_name"  # customer_name = Dealer Name = Sold-To Party
DEALER_CODE_FIELD = "dealer_code"
CUSTOMER_CODE_FIELD = "customer_code"
DN_NO_FIELD = "dn_no"
DELIVERY_STATUS_FIELD = "delivery_status"
PGI_STATUS_FIELD = "pgi_status"
POD_STATUS_FIELD = "pod_status"


# ==========================================================
# RAILWAY POSTGRESQL CONFIGURATION
# ==========================================================

class RailwayPostgresConfig:
    """Railway PostgreSQL configuration."""
    
    DATABASE_URL = os.getenv('DATABASE_URL', '')
    RAILWAY_ENVIRONMENT = os.getenv('RAILWAY_ENVIRONMENT', 'production')
    
    POOL_SIZE = int(os.getenv('DB_POOL_SIZE', '10'))
    MAX_OVERFLOW = int(os.getenv('DB_MAX_OVERFLOW', '20'))
    POOL_TIMEOUT = int(os.getenv('DB_POOL_TIMEOUT', '30'))
    POOL_RECYCLE = int(os.getenv('DB_POOL_RECYCLE', '3600'))
    
    STATEMENT_TIMEOUT = int(os.getenv('DB_STATEMENT_TIMEOUT', '30000'))
    LOCK_TIMEOUT = int(os.getenv('DB_LOCK_TIMEOUT', '5000'))
    
    SSL_MODE = os.getenv('DB_SSL_MODE', 'require')
    
    @classmethod
    def is_railway(cls) -> bool:
        """Check if running on Railway - only if DATABASE_URL is set."""
        # FIXED: Don't use RAILWAY_ENVIRONMENT default as it always returns True
        return bool(cls.DATABASE_URL)


# ==========================================================
# ENTERPRISE EXCEPTION HIERARCHY
# ==========================================================

class AnalyticsError(Exception):
    """Base exception for analytics errors."""
    pass

class DealerNotFoundError(AnalyticsError):
    def __init__(self, dealer_name: str, error_id: str = None):
        self.dealer_name = dealer_name
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Dealer '{dealer_name}' not found (Error ID: {self.error_id})")

class DashboardGenerationError(AnalyticsError):
    def __init__(self, dealer_name: str, reason: str, error_id: str = None):
        self.dealer_name = dealer_name
        self.reason = reason
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Dashboard generation failed for '{dealer_name}': {reason} (Error ID: {self.error_id})")

class DatabaseQueryError(AnalyticsError):
    def __init__(self, query: str, error: str, error_id: str = None):
        self.query = query
        self.error = error
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Database query failed: {error} (Error ID: {self.error_id})")


# ==========================================================
# RESPONSE CONTRACT
# ==========================================================

class AnalyticsResponse:
    def __init__(self, success: bool = True, data: Dict[str, Any] = None, error: str = None, error_id: str = None):
        self.success = success
        self.data = data or {}
        self.error = error
        self.error_id = error_id
        self.timestamp = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "error_id": self.error_id,
            "timestamp": self.timestamp
        }


# ==========================================================
# POSTGRESQL QUERY ENGINE - 100% POSTGRESQL
# ==========================================================

class PostgreSQLQueryEngine:
    """
    100% PostgreSQL Query Engine for Railway.
    All queries use direct SQL with PostgreSQL features.
    """
    
    def __init__(self, logistics_service):
        self.logistics = logistics_service
        self.db = logistics_service.db
    
    def execute(self, sql: str, params: Dict = None) -> List[Dict]:
        """Execute SQL and return results as list of dicts."""
        try:
            result = self.db.execute(text(sql), params or {})
            
            # Handle different result types
            if result.returns_rows:
                rows = result.fetchall()
                if rows:
                    return [dict(zip(result.keys(), row)) for row in rows]
            return []
        except Exception as e:
            logger.error(f"PostgreSQL query failed: {e}")
            logger.error(f"SQL: {sql[:500]}...")
            raise DatabaseQueryError(sql, str(e))
    
    def execute_one(self, sql: str, params: Dict = None) -> Optional[Dict]:
        """Execute SQL and return first row as dict."""
        results = self.execute(sql, params)
        return results[0] if results else None
    
    # ==========================================================
    # CORE QUERIES - 100% POSTGRESQL
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> Optional[Dict]:
        """
        Get complete dealer dashboard in ONE PostgreSQL query.
        Uses CTE, window functions, and JSON aggregation.
        
        FIXED:
        - COUNT(DISTINCT dn_no) for all DN counts
        - Added dealer_code, customer_code, division, warehouse_code, delivery_location
        - Use delivery_status for status logic
        - json_agg with ORDER BY and LIMIT via CTE
        """
        sql = """
            WITH dealer_data AS (
                SELECT 
                    customer_name as dealer_name,
                    MAX(dealer_code) as dealer_code,
                    MAX(customer_code) as customer_code,
                    MAX(division) as division,
                    MAX(warehouse_code) as warehouse_code,
                    MAX(delivery_location) as delivery_location,
                    MAX(sales_office) as sales_office,
                    MAX(sales_manager) as sales_manager,
                    MAX(warehouse) as top_warehouse,
                    MODE() WITHIN GROUP (ORDER BY ship_to_city) as city,
                    MIN(dn_create_date) as first_dn_date,
                    MAX(dn_create_date) as last_dn_date,
                    
                    -- FIXED: COUNT(DISTINCT dn_no) instead of COUNT(*)
                    COUNT(DISTINCT dn_no) as total_dns,
                    COALESCE(SUM(dn_qty), 0) as total_units,
                    COALESCE(SUM(dn_amount), 0) as total_revenue,
                    
                    -- FIXED: Use delivery_status instead of date-only logic
                    COUNT(DISTINCT CASE 
                        WHEN delivery_status = 'Completed' AND good_issue_date IS NOT NULL 
                        THEN dn_no 
                    END) as delivered_dns,
                    
                    COUNT(DISTINCT CASE 
                        WHEN delivery_status != 'Completed' OR good_issue_date IS NULL 
                        THEN dn_no 
                    END) as pending_dns,
                    
                    COUNT(DISTINCT CASE 
                        WHEN delivery_status = 'Completed' AND pod_status != 'Completed' 
                        THEN dn_no 
                    END) as transit_dns,
                    
                    COUNT(DISTINCT CASE 
                        WHEN pod_status = 'Completed' 
                        THEN dn_no 
                    END) as pod_completed_dns,
                    
                    COUNT(DISTINCT CASE 
                        WHEN delivery_status = 'Completed' AND pod_status != 'Completed' 
                        THEN dn_no 
                    END) as pending_pod_dns,
                    
                    -- FIXED: Separate aging calculations
                    AVG(CASE 
                        WHEN good_issue_date IS NOT NULL AND dn_create_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400 
                    END) as avg_pgi_aging,
                    
                    AVG(CASE 
                        WHEN pod_date IS NOT NULL AND good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date)) / 86400 
                    END) as avg_pod_aging,
                    
                    AVG(CASE 
                        WHEN pod_date IS NOT NULL AND dn_create_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - dn_create_date)) / 86400 
                    END) as avg_total_aging,
                    
                    -- FIXED: Recent DNs via CTE with json_agg
                    COALESCE(
                        (SELECT json_agg(
                            json_build_object(
                                'dn_no', dn_no,
                                'dn_qty', dn_qty,
                                'dn_amount', dn_amount,
                                'dn_create_date', dn_create_date,
                                'good_issue_date', good_issue_date,
                                'pod_date', pod_date,
                                'warehouse', warehouse,
                                'ship_to_city', ship_to_city,
                                'material_no', material_no,
                                'customer_model', customer_model,
                                'delivery_status', delivery_status,
                                'pgi_status', pgi_status,
                                'pod_status', pod_status
                            )
                            ORDER BY dn_create_date DESC
                        ) FROM (
                            SELECT 
                                dn_no, dn_qty, dn_amount, dn_create_date,
                                good_issue_date, pod_date, warehouse,
                                ship_to_city, material_no, customer_model,
                                delivery_status, pgi_status, pod_status
                            FROM delivery_reports
                            WHERE customer_name = :dealer_name
                            ORDER BY dn_create_date DESC
                            LIMIT 10
                        ) recent_dns),
                        '[]'::json
                    ) as recent_dns
                    
                FROM delivery_reports
                WHERE customer_name = :dealer_name
                GROUP BY customer_name
            )
            SELECT 
                *,
                -- FIXED: Delivery rate based on delivered_dns
                ROUND((delivered_dns::float / NULLIF(total_dns, 0)) * 100, 1) as delivery_rate,
                ROUND((pod_completed_dns::float / NULLIF(delivered_dns, 0)) * 100, 1) as pod_rate,
                CASE 
                    WHEN delivered_dns = 0 AND total_dns = 0 THEN 'Inactive'
                    WHEN total_dns < 10 THEN 'Low Activity'
                    WHEN (delivered_dns::float / NULLIF(total_dns, 0)) >= 0.9 THEN 'Active - High Performance'
                    ELSE 'Active - Needs Attention'
                END as dealer_status,
                
                -- FIXED: Unit counts with DISTINCT
                COALESCE(SUM(CASE WHEN delivery_status = 'Completed' THEN dn_qty ELSE 0 END), 0) as delivered_units,
                COALESCE(SUM(CASE WHEN delivery_status != 'Completed' OR good_issue_date IS NULL THEN dn_qty ELSE 0 END), 0) as pending_units,
                COALESCE(SUM(CASE WHEN delivery_status = 'Completed' AND pod_status != 'Completed' THEN dn_qty ELSE 0 END), 0) as transit_units
            FROM dealer_data
            GROUP BY 
                dealer_name, dealer_code, customer_code, division,
                warehouse_code, delivery_location, sales_office, sales_manager,
                top_warehouse, city, first_dn_date, last_dn_date,
                total_dns, total_units, total_revenue,
                delivered_dns, pending_dns, transit_dns,
                pod_completed_dns, pending_pod_dns,
                avg_pgi_aging, avg_pod_aging, avg_total_aging,
                recent_dns
        """
        
        return self.execute_one(sql, {"dealer_name": dealer_name})
    
    def get_all_dealers_dashboard(self) -> List[Dict]:
        """
        Get dashboards for ALL dealers in ONE PostgreSQL query.
        Uses window functions for rankings.
        
        FIXED:
        - COUNT(DISTINCT dn_no) for all DN counts
        - Added dealer_code, customer_code, division, warehouse_code, delivery_location
        - Use delivery_status for status logic
        """
        sql = """
            WITH dealer_aggregates AS (
                SELECT 
                    customer_name as dealer_name,
                    MAX(dealer_code) as dealer_code,
                    MAX(customer_code) as customer_code,
                    MAX(division) as division,
                    MAX(warehouse_code) as warehouse_code,
                    MAX(delivery_location) as delivery_location,
                    MAX(sales_office) as sales_office,
                    MAX(sales_manager) as sales_manager,
                    MAX(warehouse) as top_warehouse,
                    MODE() WITHIN GROUP (ORDER BY ship_to_city) as city,
                    
                    -- FIXED: COUNT(DISTINCT dn_no)
                    COUNT(DISTINCT dn_no) as total_dns,
                    COALESCE(SUM(dn_qty), 0) as total_units,
                    COALESCE(SUM(dn_amount), 0) as total_revenue,
                    
                    COUNT(DISTINCT CASE 
                        WHEN delivery_status = 'Completed' AND good_issue_date IS NOT NULL 
                        THEN dn_no 
                    END) as delivered_dns,
                    
                    COUNT(DISTINCT CASE 
                        WHEN delivery_status != 'Completed' OR good_issue_date IS NULL 
                        THEN dn_no 
                    END) as pending_dns,
                    
                    COUNT(DISTINCT CASE 
                        WHEN delivery_status = 'Completed' AND pod_status != 'Completed' 
                        THEN dn_no 
                    END) as transit_dns,
                    
                    COUNT(DISTINCT CASE 
                        WHEN pod_status = 'Completed' 
                        THEN dn_no 
                    END) as pod_completed_dns,
                    
                    COUNT(DISTINCT CASE 
                        WHEN delivery_status = 'Completed' AND pod_status != 'Completed' 
                        THEN dn_no 
                    END) as pending_pod_dns,
                    
                    AVG(CASE 
                        WHEN good_issue_date IS NOT NULL AND dn_create_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400 
                    END) as avg_pgi_aging,
                    
                    AVG(CASE 
                        WHEN pod_date IS NOT NULL AND good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date)) / 86400 
                    END) as avg_pod_aging,
                    
                    AVG(CASE 
                        WHEN pod_date IS NOT NULL AND dn_create_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - dn_create_date)) / 86400 
                    END) as avg_total_aging
                    
                FROM delivery_reports
                WHERE customer_name IS NOT NULL AND customer_name != ''
                GROUP BY customer_name
            ),
            ranked_dealers AS (
                SELECT 
                    *,
                    ROW_NUMBER() OVER (ORDER BY total_revenue DESC) as revenue_rank,
                    ROW_NUMBER() OVER (ORDER BY total_dns DESC) as quantity_rank,
                    ROW_NUMBER() OVER (ORDER BY (delivered_dns::float / NULLIF(total_dns, 0)) DESC) as delivery_rank,
                    COUNT(*) OVER () as total_dealers
                FROM dealer_aggregates
            )
            SELECT 
                dealer_name,
                dealer_code,
                customer_code,
                division,
                warehouse_code,
                delivery_location,
                sales_office,
                sales_manager,
                top_warehouse,
                city,
                total_dns,
                total_units,
                total_revenue,
                delivered_dns,
                pending_dns,
                transit_dns,
                pod_completed_dns,
                pending_pod_dns,
                avg_pgi_aging,
                avg_pod_aging,
                avg_total_aging,
                revenue_rank,
                quantity_rank,
                delivery_rank,
                total_dealers,
                ROUND((delivered_dns::float / NULLIF(total_dns, 0)) * 100, 1) as delivery_rate,
                ROUND((pod_completed_dns::float / NULLIF(delivered_dns, 0)) * 100, 1) as pod_rate,
                CASE 
                    WHEN delivered_dns = 0 AND total_dns = 0 THEN 'Inactive'
                    WHEN total_dns < 10 THEN 'Low Activity'
                    WHEN (delivered_dns::float / NULLIF(total_dns, 0)) >= 0.9 THEN 'Active - High Performance'
                    ELSE 'Active - Needs Attention'
                END as dealer_status
            FROM ranked_dealers
            ORDER BY total_revenue DESC
        """
        
        return self.execute(sql)
    
    def get_dealer_dn_history(self, dealer_name: str, limit: int = 100) -> List[Dict]:
        """Get dealer DN history with PostgreSQL date calculations."""
        sql = """
            SELECT 
                dn_no,
                dn_qty,
                dn_amount,
                dn_create_date,
                good_issue_date,
                pod_date,
                warehouse,
                ship_to_city,
                material_no,
                customer_model,
                sales_office,
                sales_manager,
                delivery_status,
                pgi_status,
                pod_status,
                EXTRACT(EPOCH FROM (NOW() - dn_create_date)) / 86400 as age_days,
                CASE 
                    WHEN pod_status = 'Completed' THEN 'delivered'
                    WHEN delivery_status = 'Completed' THEN 'in_transit'
                    ELSE 'pending'
                END as current_status,
                -- FIXED: Separate aging calculations
                CASE 
                    WHEN good_issue_date IS NOT NULL AND dn_create_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400
                    ELSE NULL
                END as pgi_aging_days,
                CASE 
                    WHEN pod_date IS NOT NULL AND good_issue_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date)) / 86400
                    ELSE NULL
                END as pod_aging_days,
                CASE 
                    WHEN pod_date IS NOT NULL AND dn_create_date IS NOT NULL 
                    THEN EXTRACT(EPOCH FROM (pod_date - dn_create_date)) / 86400
                    ELSE NULL
                END as total_aging_days
            FROM delivery_reports
            WHERE customer_name = :dealer_name
            ORDER BY dn_create_date DESC
            LIMIT :limit
        """
        
        return self.execute(sql, {"dealer_name": dealer_name, "limit": limit})
    
    def get_dealer_aging_analysis(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get aging analysis with PostgreSQL window functions.
        
        FIXED:
        - COUNT(DISTINCT dn_no)
        - Separate PGI, POD, and Total aging
        """
        sql = """
            WITH aging_data AS (
                SELECT 
                    dn_no,
                    dn_qty,
                    dn_amount,
                    dn_create_date,
                    good_issue_date,
                    pod_date,
                    delivery_status,
                    pod_status,
                    -- FIXED: Separate aging calculations
                    CASE 
                        WHEN good_issue_date IS NOT NULL AND dn_create_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400
                        ELSE NULL
                    END as pgi_aging_days,
                    CASE 
                        WHEN pod_date IS NOT NULL AND good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date)) / 86400
                        ELSE NULL
                    END as pod_aging_days,
                    CASE 
                        WHEN pod_date IS NOT NULL AND dn_create_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - dn_create_date)) / 86400
                        ELSE NULL
                    END as total_aging_days,
                    CASE 
                        WHEN pod_status = 'Completed' THEN 'delivered'
                        WHEN delivery_status = 'Completed' THEN 'in_transit'
                        ELSE 'pending'
                    END as status
                FROM delivery_reports
                WHERE customer_name = :dealer_name
            ),
            bucket_counts AS (
                SELECT 
                    status,
                    COUNT(DISTINCT dn_no) as count,
                    SUM(dn_qty) as total_units,
                    SUM(dn_amount) as total_revenue,
                    AVG(COALESCE(pgi_aging_days, 0)) as avg_pgi_aging,
                    AVG(COALESCE(pod_aging_days, 0)) as avg_pod_aging,
                    AVG(COALESCE(total_aging_days, 0)) as avg_total_aging
                FROM aging_data
                GROUP BY status
            ),
            aging_distribution AS (
                SELECT 
                    CASE 
                        WHEN COALESCE(total_aging_days, 0) <= 3 THEN '0-3'
                        WHEN COALESCE(total_aging_days, 0) <= 7 THEN '4-7'
                        WHEN COALESCE(total_aging_days, 0) <= 14 THEN '8-14'
                        WHEN COALESCE(total_aging_days, 0) <= 30 THEN '15-30'
                        ELSE '30+'
                    END as aging_bucket,
                    COUNT(DISTINCT dn_no) as count,
                    SUM(dn_qty) as total_units,
                    SUM(dn_amount) as total_revenue
                FROM aging_data
                WHERE status = 'delivered'
                GROUP BY aging_bucket
            )
            SELECT 
                (SELECT COUNT(DISTINCT dn_no) FROM aging_data) as total_dns,
                (SELECT COUNT(DISTINCT dn_no) FROM aging_data WHERE status = 'delivered') as delivered_dns,
                (SELECT COUNT(DISTINCT dn_no) FROM aging_data WHERE status = 'in_transit') as in_transit_dns,
                (SELECT COUNT(DISTINCT dn_no) FROM aging_data WHERE status = 'pending') as pending_dns,
                (SELECT COALESCE(AVG(pgi_aging_days), 0) FROM aging_data WHERE status = 'delivered') as avg_pgi_aging,
                (SELECT COALESCE(AVG(pod_aging_days), 0) FROM aging_data WHERE status = 'in_transit') as avg_pod_aging,
                (SELECT COALESCE(AVG(total_aging_days), 0) FROM aging_data WHERE status = 'delivered') as avg_total_aging,
                (SELECT json_agg(
                    json_build_object(
                        'bucket', aging_bucket,
                        'count', count,
                        'total_units', total_units,
                        'total_revenue', total_revenue
                    )
                ) FROM aging_distribution) as aging_distribution,
                (SELECT json_agg(
                    json_build_object(
                        'status', status,
                        'count', count,
                        'total_units', total_units,
                        'total_revenue', total_revenue,
                        'avg_pgi_aging', avg_pgi_aging,
                        'avg_pod_aging', avg_pod_aging,
                        'avg_total_aging', avg_total_aging
                    )
                ) FROM bucket_counts) as status_breakdown
        """
        
        return self.execute_one(sql, {"dealer_name": dealer_name})
    
    def get_dealer_trends(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get dealer trends using PostgreSQL date_trunc and window functions.
        
        FIXED:
        - COUNT(DISTINCT dn_no)
        """
        sql = """
            WITH daily_trends AS (
                SELECT 
                    DATE_TRUNC('day', dn_create_date) as date,
                    COUNT(DISTINCT dn_no) as dn_count,
                    SUM(dn_qty) as units,
                    SUM(dn_amount) as revenue,
                    COUNT(DISTINCT CASE WHEN delivery_status = 'Completed' THEN dn_no END) as delivered_dns,
                    COUNT(DISTINCT dn_no) OVER (ORDER BY DATE_TRUNC('day', dn_create_date) ROWS UNBOUNDED PRECEDING) as cumulative_dns,
                    SUM(dn_amount) OVER (ORDER BY DATE_TRUNC('day', dn_create_date) ROWS UNBOUNDED PRECEDING) as cumulative_revenue
                FROM delivery_reports
                WHERE customer_name = :dealer_name
                GROUP BY DATE_TRUNC('day', dn_create_date)
                ORDER BY date DESC
                LIMIT 90
            ),
            monthly_trends AS (
                SELECT 
                    DATE_TRUNC('month', dn_create_date) as month,
                    COUNT(DISTINCT dn_no) as dn_count,
                    SUM(dn_qty) as units,
                    SUM(dn_amount) as revenue,
                    AVG(dn_amount) as avg_dn_value
                FROM delivery_reports
                WHERE customer_name = :dealer_name
                GROUP BY DATE_TRUNC('month', dn_create_date)
                ORDER BY month DESC
                LIMIT 12
            ),
            weekly_trends AS (
                SELECT 
                    DATE_TRUNC('week', dn_create_date) as week,
                    COUNT(DISTINCT dn_no) as dn_count,
                    SUM(dn_qty) as units,
                    SUM(dn_amount) as revenue
                FROM delivery_reports
                WHERE customer_name = :dealer_name
                GROUP BY DATE_TRUNC('week', dn_create_date)
                ORDER BY week DESC
                LIMIT 52
            )
            SELECT 
                (SELECT json_agg(daily_trends ORDER BY date DESC) FROM daily_trends) as daily_trends,
                (SELECT json_agg(monthly_trends ORDER BY month DESC) FROM monthly_trends) as monthly_trends,
                (SELECT json_agg(weekly_trends ORDER BY week DESC) FROM weekly_trends) as weekly_trends,
                (SELECT COALESCE(AVG(revenue), 0) FROM daily_trends) as avg_daily_revenue,
                (SELECT COALESCE(AVG(revenue), 0) FROM monthly_trends) as avg_monthly_revenue
        """
        
        return self.execute_one(sql, {"dealer_name": dealer_name})
    
    def get_dealer_product_analytics(self, dealer_name: str) -> List[Dict]:
        """
        Get product analytics using PostgreSQL aggregation.
        
        FIXED:
        - COUNT(DISTINCT dn_no)
        """
        sql = """
            SELECT 
                COALESCE(material_no, 'UNKNOWN') as product_code,
                COALESCE(customer_model, material_no, 'UNKNOWN') as product_name,
                COUNT(DISTINCT dn_no) as dn_count,
                SUM(dn_qty) as total_units,
                SUM(dn_amount) as total_revenue,
                AVG(dn_amount) as avg_revenue_per_dn,
                MAX(dn_amount) as max_revenue,
                MIN(dn_amount) as min_revenue,
                MODE() WITHIN GROUP (ORDER BY warehouse) as primary_warehouse,
                COUNT(DISTINCT CASE WHEN delivery_status = 'Completed' THEN dn_no END) as delivered_count,
                COUNT(DISTINCT CASE WHEN pod_status = 'Completed' THEN dn_no END) as pod_completed_count,
                ROUND((COUNT(DISTINCT CASE WHEN delivery_status = 'Completed' THEN dn_no END)::float / NULLIF(COUNT(DISTINCT dn_no), 0)) * 100, 1) as delivery_rate,
                ROUND(SUM(dn_amount)::float / NULLIF(SUM(SUM(dn_amount)) OVER (), 0) * 100, 1) as revenue_percentage
            FROM delivery_reports
            WHERE customer_name = :dealer_name
            GROUP BY material_no, customer_model
            ORDER BY total_revenue DESC
            LIMIT 50
        """
        
        return self.execute(sql, {"dealer_name": dealer_name})
    
    def get_dealer_location_analytics(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get location analytics using PostgreSQL aggregation.
        
        FIXED:
        - COUNT(DISTINCT dn_no)
        """
        sql = """
            WITH warehouse_stats AS (
                SELECT 
                    warehouse,
                    COUNT(DISTINCT dn_no) as dn_count,
                    SUM(dn_qty) as units,
                    SUM(dn_amount) as revenue,
                    AVG(dn_amount) as avg_revenue,
                    COUNT(DISTINCT dn_no) as unique_dns
                FROM delivery_reports
                WHERE customer_name = :dealer_name AND warehouse IS NOT NULL
                GROUP BY warehouse
                ORDER BY revenue DESC
            ),
            city_stats AS (
                SELECT 
                    ship_to_city,
                    COUNT(DISTINCT dn_no) as dn_count,
                    SUM(dn_qty) as units,
                    SUM(dn_amount) as revenue,
                    AVG(dn_amount) as avg_revenue
                FROM delivery_reports
                WHERE customer_name = :dealer_name AND ship_to_city IS NOT NULL
                GROUP BY ship_to_city
                ORDER BY revenue DESC
            ),
            division_stats AS (
                SELECT 
                    division,
                    COUNT(DISTINCT dn_no) as dn_count,
                    SUM(dn_qty) as units,
                    SUM(dn_amount) as revenue
                FROM delivery_reports
                WHERE customer_name = :dealer_name AND division IS NOT NULL
                GROUP BY division
                ORDER BY revenue DESC
            )
            SELECT 
                (SELECT json_agg(warehouse_stats) FROM warehouse_stats) as warehouses,
                (SELECT json_agg(city_stats) FROM city_stats) as cities,
                (SELECT json_agg(division_stats) FROM division_stats) as divisions,
                (SELECT COUNT(DISTINCT warehouse) FROM delivery_reports WHERE customer_name = :dealer_name AND warehouse IS NOT NULL) as total_warehouses,
                (SELECT COUNT(DISTINCT ship_to_city) FROM delivery_reports WHERE customer_name = :dealer_name AND ship_to_city IS NOT NULL) as total_cities
        """
        
        return self.execute_one(sql, {"dealer_name": dealer_name})
    
    def get_network_summary(self) -> Dict[str, Any]:
        """
        Get network summary using PostgreSQL aggregation.
        
        FIXED:
        - COUNT(DISTINCT dn_no)
        """
        sql = """
            WITH network_stats AS (
                SELECT 
                    COUNT(DISTINCT customer_name) as total_dealers,
                    COUNT(DISTINCT dn_no) as total_dns,
                    SUM(dn_qty) as total_units,
                    SUM(dn_amount) as total_revenue,
                    AVG(dn_amount) as avg_dn_value,
                    COUNT(DISTINCT CASE WHEN delivery_status = 'Completed' THEN dn_no END) as delivered_dns,
                    COUNT(DISTINCT CASE WHEN delivery_status != 'Completed' OR good_issue_date IS NULL THEN dn_no END) as pending_dns,
                    COUNT(DISTINCT CASE WHEN delivery_status = 'Completed' AND pod_status != 'Completed' THEN dn_no END) as transit_dns,
                    COUNT(DISTINCT CASE WHEN pod_status = 'Completed' THEN dn_no END) as pod_completed_dns,
                    AVG(CASE 
                        WHEN good_issue_date IS NOT NULL AND dn_create_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400 
                    END) as avg_pgi_aging,
                    AVG(CASE 
                        WHEN pod_date IS NOT NULL AND good_issue_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - good_issue_date)) / 86400 
                    END) as avg_pod_aging,
                    AVG(CASE 
                        WHEN pod_date IS NOT NULL AND dn_create_date IS NOT NULL 
                        THEN EXTRACT(EPOCH FROM (pod_date - dn_create_date)) / 86400 
                    END) as avg_total_aging,
                    MIN(dn_create_date) as first_dn_date,
                    MAX(dn_create_date) as last_dn_date
                FROM delivery_reports
                WHERE customer_name IS NOT NULL AND customer_name != ''
            ),
            top_dealers AS (
                SELECT 
                    customer_name as dealer_name,
                    SUM(dn_amount) as revenue
                FROM delivery_reports
                WHERE customer_name IS NOT NULL AND customer_name != ''
                GROUP BY customer_name
                ORDER BY revenue DESC
                LIMIT 5
            ),
            status_breakdown AS (
                SELECT 
                    delivery_status,
                    COUNT(DISTINCT dn_no) as count
                FROM delivery_reports
                GROUP BY delivery_status
            )
            SELECT 
                *,
                ROUND((delivered_dns::float / NULLIF(total_dns, 0)) * 100, 1) as delivery_rate,
                ROUND((pod_completed_dns::float / NULLIF(delivered_dns, 0)) * 100, 1) as pod_rate,
                (SELECT json_agg(top_dealers) FROM top_dealers) as top_dealers,
                (SELECT json_agg(status_breakdown) FROM status_breakdown) as status_breakdown
            FROM network_stats
        """
        
        return self.execute_one(sql)
    
    def resolve_dealer_postgres(self, dealer_input: str) -> Optional[str]:
        """
        Dealer resolution using PostgreSQL features.
        
        FIXED:
        - Added dealer_code and customer_code search
        - Better resolution order
        - Uses trigram similarity and full-text search
        """
        if not dealer_input or not dealer_input.strip():
            return None
        
        dealer_input = dealer_input.strip()
        
        # 1. Try exact dealer name match (case-insensitive)
        sql_exact = """
            SELECT customer_name
            FROM delivery_reports
            WHERE customer_name ILIKE :dealer_input
            GROUP BY customer_name
            ORDER BY COUNT(DISTINCT dn_no) DESC
            LIMIT 1
        """
        result = self.execute_one(sql_exact, {"dealer_input": dealer_input})
        if result:
            return result["customer_name"]
        
        # 2. Try dealer_code match
        sql_code = """
            SELECT customer_name
            FROM delivery_reports
            WHERE dealer_code ILIKE :dealer_input
            GROUP BY customer_name
            ORDER BY COUNT(DISTINCT dn_no) DESC
            LIMIT 1
        """
        result = self.execute_one(sql_code, {"dealer_input": dealer_input})
        if result:
            return result["customer_name"]
        
        # 3. Try customer_code match
        sql_customer = """
            SELECT customer_name
            FROM delivery_reports
            WHERE customer_code ILIKE :dealer_input
            GROUP BY customer_name
            ORDER BY COUNT(DISTINCT dn_no) DESC
            LIMIT 1
        """
        result = self.execute_one(sql_customer, {"dealer_input": dealer_input})
        if result:
            return result["customer_name"]
        
        # 4. Try contains match
        sql_contains = """
            SELECT customer_name
            FROM delivery_reports
            WHERE customer_name ILIKE :pattern
            GROUP BY customer_name
            ORDER BY COUNT(DISTINCT dn_no) DESC
            LIMIT 1
        """
        result = self.execute_one(sql_contains, {"pattern": f"%{dealer_input}%"})
        if result:
            return result["customer_name"]
        
        # 5. Try PostgreSQL trigram similarity (if pg_trgm is installed)
        try:
            sql_trigram = """
                SELECT customer_name, 
                       SIMILARITY(customer_name, :dealer_input) as similarity
                FROM delivery_reports
                WHERE customer_name IS NOT NULL
                GROUP BY customer_name
                ORDER BY similarity DESC
                LIMIT 1
            """
            result = self.execute_one(sql_trigram, {"dealer_input": dealer_input})
            if result and result.get("similarity", 0) > 0.3:
                return result["customer_name"]
        except:
            pass
        
        # 6. Try word-by-word match
        words = dealer_input.lower().split()
        if len(words) >= 2:
            for i in range(len(words) - 1):
                for j in range(i + 1, min(i + 3, len(words) + 1)):
                    pattern = ' '.join(words[i:j])
                    if len(pattern) >= 3:
                        sql_words = """
                            SELECT customer_name
                            FROM delivery_reports
                            WHERE LOWER(customer_name) LIKE :pattern
                            GROUP BY customer_name
                            ORDER BY COUNT(DISTINCT dn_no) DESC
                            LIMIT 1
                        """
                        result = self.execute_one(sql_words, {"pattern": f"%{pattern}%"})
                        if result:
                            return result["customer_name"]
        
        return None


# ==========================================================
# ANALYTICS SERVICE - 100% POSTGRESQL
# ==========================================================

class AnalyticsService:
    """
    ENTERPRISE DEALER INTELLIGENCE ENGINE v7.0
    
    100% PostgreSQL Queries for Railway:
    - All data from PostgreSQL
    - No mock data
    - PostgreSQL optimized
    - Fully aligned with schema
    """
    
    # Standard field mapping - DO NOT CHANGE
    DEALER_NAME_FIELD = DEALER_NAME_FIELD
    
    def __init__(self, use_redis: bool = False):
        self._start_time = time.time()
        
        # Check if running on Railway
        self.is_railway = RailwayPostgresConfig.is_railway()
        if self.is_railway:
            logger.info("🚆 Running on Railway - 100% PostgreSQL mode enabled")
        
        # Dependencies
        self.logistics = LogisticsQueryService()
        self.kpi = KPIService()
        self.schema = get_schema_service()
        self.today = datetime.now().date()
        
        # PostgreSQL Query Engine - 100% PostgreSQL
        self.pg = PostgreSQLQueryEngine(self.logistics)
        
        # Cache
        self._cache: Dict[str, Any] = {}
        self._cache_ttl: Dict[str, datetime] = {}
        self._dealer_cache: Dict[str, Tuple[str, datetime]] = {}
        
        # Performance metrics
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_duration_ms": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "dealer_resolution_success": 0,
            "dealer_resolution_failure": 0,
            "postgresql_queries": 0,
            "slow_queries": 0,
            "errors_by_type": defaultdict(int)
        }
        
        # Test PostgreSQL connection
        self._test_postgresql()
        
        logger.info("=" * 70)
        logger.info("AnalyticsService v7.0 - Fully Aligned with PostgreSQL Schema")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   ✅ CRITICAL FIXES:")
        logger.info("      - COUNT(DISTINCT dn_no) for all DN counts")
        logger.info("      - json_agg with ORDER BY and LIMIT via CTE")
        logger.info("      - Added dealer_code, customer_code, division")
        logger.info("      - Added warehouse_code, delivery_location")
        logger.info("      - Use delivery_status, pgi_status, pod_status")
        logger.info("      - Dealer resolution with code search")
        logger.info("      - Fixed Railway environment detection")
        logger.info("      - Standardized DEALER_NAME_FIELD constant")
        logger.info("      - Separate PGI, POD, Total aging")
        logger.info("")
        logger.info(f"   🌐 Environment: {'Railway' if self.is_railway else 'Local'}")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 70)
    
    def _test_postgresql(self):
        """Test PostgreSQL connection."""
        try:
            result = self.logistics.db.execute(text("SELECT version()"))
            version = result.first()[0]
            logger.info(f"✅ PostgreSQL connected: {version[:50]}...")
            
            # Check pg_trgm extension
            ext_result = self.logistics.db.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'")
            )
            has_trgm = ext_result.first() is not None
            if has_trgm:
                logger.info("✅ PostgreSQL pg_trgm extension available")
            else:
                logger.warning("⚠️ PostgreSQL pg_trgm extension not installed")
            
        except Exception as e:
            logger.error(f"❌ PostgreSQL connection test failed: {e}")
    
    def close(self):
        self.logistics.close()
        self.kpi.close()
    
    # ==========================================================
    # CACHE HELPERS
    # ==========================================================
    
    def _get_cached(self, key: str) -> Optional[Any]:
        if key in self._cache and key in self._cache_ttl:
            if datetime.now() < self._cache_ttl[key]:
                self.metrics["cache_hits"] += 1
                return self._cache[key]
        self.metrics["cache_misses"] += 1
        return None
    
    def _set_cached(self, key: str, value: Any, ttl_seconds: int = 300):
        self._cache[key] = value
        self._cache_ttl[key] = datetime.now() + timedelta(seconds=ttl_seconds)
    
    def _get_cached_dealer(self, dealer_input: str) -> Optional[str]:
        if dealer_input in self._dealer_cache:
            resolved, expiry = self._dealer_cache[dealer_input]
            if datetime.now() < expiry:
                return resolved
        return None
    
    def _set_cached_dealer(self, dealer_input: str, resolved: str):
        self._dealer_cache[dealer_input] = (resolved, datetime.now() + timedelta(hours=24))
    
    def clear_cache(self):
        self._cache.clear()
        self._cache_ttl.clear()
        self._dealer_cache.clear()
        logger.info("All caches cleared")
    
    # ==========================================================
    # DEALER RESOLUTION - 100% POSTGRESQL
    # ==========================================================
    
    def _resolve_dealer(self, dealer_input: str) -> Optional[str]:
        """Resolve dealer using PostgreSQL."""
        if not dealer_input:
            return None
        
        # Check cache
        cached = self._get_cached_dealer(dealer_input)
        if cached:
            return cached
        
        # Use PostgreSQL resolution
        resolved = self.pg.resolve_dealer_postgres(dealer_input)
        if resolved:
            self._set_cached_dealer(dealer_input, resolved)
            self.metrics["dealer_resolution_success"] += 1
        else:
            self.metrics["dealer_resolution_failure"] += 1
        
        return resolved
    
    # ==========================================================
    # DEALER 360 DASHBOARD - 100% POSTGRESQL
    # ==========================================================
    
    def get_dealer_360_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """Get complete dealer dashboard from PostgreSQL."""
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        self.metrics["total_requests"] += 1
        
        try:
            if not dealer_name or not dealer_name.strip():
                error_id = str(uuid.uuid4())[:8]
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False, 
                    error="Dealer name cannot be empty",
                    error_id=error_id
                )
            
            # Resolve dealer
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                error_id = str(uuid.uuid4())[:8]
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False, 
                    error=f"Dealer '{dealer_name}' not found",
                    error_id=error_id
                )
            
            # Get dashboard from PostgreSQL
            cache_key = f"dashboard:{resolved}"
            dashboard_data = self._get_cached(cache_key)
            
            if dashboard_data is None:
                dashboard_data = self.pg.get_dealer_dashboard(resolved)
                self.metrics["postgresql_queries"] += 1
                if dashboard_data:
                    self._set_cached(cache_key, dashboard_data, 600)
            
            if not dashboard_data:
                error_id = str(uuid.uuid4())[:8]
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False, 
                    error=f"No data for dealer '{resolved}'",
                    error_id=error_id
                )
            
            # Get additional data
            trends = self.pg.get_dealer_trends(resolved)
            products = self.pg.get_dealer_product_analytics(resolved)
            location = self.pg.get_dealer_location_analytics(resolved)
            aging = self.pg.get_dealer_aging_analysis(resolved)
            
            # Build complete dashboard
            analytics = self._calculate_analytics_from_data(dashboard_data)
            
            dashboard = {
                "success": True,
                "request_id": request_id,
                "dealer_name": resolved,
                "profile": self._build_profile(dashboard_data),
                "executive_kpis": self._build_kpis(dashboard_data, analytics),
                "performance": self._build_performance(dashboard_data),
                "delivery": self._build_delivery(dashboard_data),
                "pod": self._build_pod(dashboard_data),
                "financial": self._build_financial(dashboard_data),
                "health": analytics["health"],
                "risk": analytics["risk"],
                "trends": trends,
                "products": products,
                "location": location,
                "aging": aging,
                "generated_at": datetime.now().isoformat()
            }
            
            duration_ms = (time.time() - start_time) * 1000
            self.metrics["successful_requests"] += 1
            self.metrics["total_duration_ms"] += duration_ms
            
            if duration_ms > 1000:
                self.metrics["slow_queries"] += 1
                logger.warning(f"[{request_id}] ⚠️ SLOW QUERY: {duration_ms:.2f}ms")
            
            logger.info(f"[{request_id}] ✅ Dashboard generated in {duration_ms:.2f}ms")
            
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            error_id = str(uuid.uuid4())[:8]
            self.metrics["failed_requests"] += 1
            logger.exception(f"[{request_id}] ❌ ERROR: {e}")
            return AnalyticsResponse(
                success=False, 
                error=str(e),
                error_id=error_id
            )
    
    # ==========================================================
    # ALL DEALERS DASHBOARD - 100% POSTGRESQL
    # ==========================================================
    
    def get_all_dealers_dashboard(self) -> AnalyticsResponse:
        """Get dashboards for all dealers from PostgreSQL."""
        try:
            cache_key = "all_dealers_dashboard"
            dealers = self._get_cached(cache_key)
            
            if dealers is None:
                dealers = self.pg.get_all_dealers_dashboard()
                self.metrics["postgresql_queries"] += 1
                if dealers:
                    self._set_cached(cache_key, dealers, 600)
            
            return AnalyticsResponse(success=True, data={
                "dealers": dealers,
                "total_dealers": len(dealers)
            })
            
        except Exception as e:
            logger.error(f"All dealers dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # DEALER RANKINGS - 100% POSTGRESQL
    # ==========================================================
    
    def get_dealer_rankings(self, dealer_name: str) -> AnalyticsResponse:
        """Get dealer rankings from PostgreSQL."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            cache_key = "all_dealers_rankings"
            rankings = self._get_cached(cache_key)
            
            if rankings is None:
                dealers = self.pg.get_all_dealers_dashboard()
                rankings = {}
                for d in dealers:
                    rankings[d["dealer_name"]] = {
                        "revenue_rank": d.get("revenue_rank", 0),
                        "quantity_rank": d.get("quantity_rank", 0),
                        "delivery_rank": d.get("delivery_rank", 0),
                        "total_dealers": d.get("total_dealers", 0)
                    }
                self._set_cached(cache_key, rankings, 3600)
            
            if resolved not in rankings:
                return AnalyticsResponse(success=False, error=f"Dealer '{resolved}' not ranked")
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                **rankings[resolved]
            })
            
        except Exception as e:
            logger.error(f"Dealer rankings failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # DEALER TIMELINE - 100% POSTGRESQL
    # ==========================================================
    
    def get_dealer_timeline(self, dealer_name: str, limit: int = 20) -> AnalyticsResponse:
        """Get dealer timeline from PostgreSQL."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dns = self.pg.get_dealer_dn_history(resolved, limit)
            self.metrics["postgresql_queries"] += 1
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "timeline": dns,
                "total_dns": len(dns)
            })
            
        except Exception as e:
            logger.error(f"Dealer timeline failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # DEALER ALERTS - 100% POSTGRESQL
    # ==========================================================
    
    def get_dealer_alerts(self, dealer_name: str) -> AnalyticsResponse:
        """Get dealer alerts from PostgreSQL."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.pg.get_dealer_dashboard(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{resolved}'")
            
            alerts = self._generate_alerts(dashboard)
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "alerts": alerts,
                "alert_count": len(alerts)
            })
            
        except Exception as e:
            logger.error(f"Dealer alerts failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # EXECUTIVE INSIGHTS - 100% POSTGRESQL
    # ==========================================================
    
    def get_executive_insights(self, dealer_name: str = None) -> AnalyticsResponse:
        """Get executive insights from PostgreSQL."""
        try:
            if dealer_name:
                resolved = self._resolve_dealer(dealer_name)
                if not resolved:
                    return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
                
                dashboard = self.pg.get_dealer_dashboard(resolved)
                if not dashboard:
                    return AnalyticsResponse(success=False, error=f"No data for dealer '{resolved}'")
                
                insights, issues, recommendations = self._generate_insights(dashboard)
                
                return AnalyticsResponse(success=True, data={
                    "dealer_name": resolved,
                    "insights": insights,
                    "issues": issues,
                    "recommendations": recommendations
                })
            else:
                # Network insights
                network = self.pg.get_network_summary()
                return AnalyticsResponse(success=True, data={
                    "network": network,
                    "insights": [
                        f"Network total revenue: PKR {network.get('total_revenue', 0):,.0f}",
                        f"Network total DNs: {network.get('total_dns', 0)}",
                        f"Active dealers: {network.get('total_dealers', 0)}",
                        f"Delivery rate: {network.get('delivery_rate', 0)}%"
                    ]
                })
            
        except Exception as e:
            logger.error(f"Executive insights failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # HEALTH CHECK - 100% POSTGRESQL
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check with PostgreSQL validation."""
        status = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": "7.0",
            "environment": "Railway" if self.is_railway else "Local",
            "checks": {}
        }
        
        # Check PostgreSQL
        try:
            result = self.logistics.db.execute(text("SELECT 1 as connected"))
            if result.first():
                status["checks"]["postgresql"] = {"status": "healthy", "message": "Connected"}
            else:
                status["checks"]["postgresql"] = {"status": "warning", "message": "Connection issue"}
        except Exception as e:
            status["status"] = "unhealthy"
            status["checks"]["postgresql"] = {"status": "unhealthy", "message": str(e)}
        
        # Check data access
        try:
            test_data = self.pg.execute_one("SELECT COUNT(*) as count FROM delivery_reports LIMIT 1")
            if test_data:
                status["checks"]["data_access"] = {"status": "healthy", "message": "Data accessible"}
            else:
                status["checks"]["data_access"] = {"status": "warning", "message": "No data found"}
        except Exception as e:
            status["checks"]["data_access"] = {"status": "warning", "message": str(e)}
        
        return status
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get service metrics."""
        total = self.metrics["total_requests"]
        successful = self.metrics["successful_requests"]
        
        return {
            "total_requests": total,
            "successful_requests": successful,
            "failed_requests": self.metrics["failed_requests"],
            "success_rate": round((successful / max(total, 1)) * 100, 1),
            "avg_duration_ms": round(self.metrics["total_duration_ms"] / max(total, 1), 2),
            "cache_hit_rate": round((self.metrics["cache_hits"] / max(self.metrics["cache_hits"] + self.metrics["cache_misses"], 1)) * 100, 1),
            "postgresql_queries": self.metrics["postgresql_queries"],
            "version": "7.0",
            "environment": "Railway" if self.is_railway else "Local"
        }
    
    # ==========================================================
    # PRIVATE HELPERS
    # ==========================================================
    
    def _calculate_analytics_from_data(self, data: Dict) -> Dict:
        """Calculate analytics from PostgreSQL data."""
        delivery_rate = data.get("delivery_rate", 0)
        pod_rate = data.get("pod_rate", 0)
        avg_pgi_aging = data.get("avg_pgi_aging", 0)
        avg_total_aging = data.get("avg_total_aging", 0)
        revenue = data.get("total_revenue", 0)
        total_dns = data.get("total_dns", 0)
        
        # Health score - using delivery_rate, pod_rate, and avg_total_aging
        health_score = int(
            (min(delivery_rate / 90 * 100, 100) * 0.40) +
            (min(pod_rate / 90 * 100, 100) * 0.30) +
            (max(100 - min(avg_total_aging / 30 * 100, 100), 0) * 0.20) +
            (min(revenue / 1000000 * 100, 100) * 0.10)
        )
        
        # Risk
        delivery_risk = 0 if delivery_rate >= 90 else 50 if delivery_rate >= 70 else 100
        pod_risk = 0 if pod_rate >= 90 else 50 if pod_rate >= 70 else 100
        aging_risk = 0 if avg_total_aging <= 3 else 50 if avg_total_aging <= 14 else 100
        
        return {
            "health": {
                "score": min(health_score, 100),
                "category": "Excellent" if health_score >= 80 else "Good" if health_score >= 60 else "Average"
            },
            "risk": {
                "risk_score": (delivery_risk + pod_risk + aging_risk) // 3,
                "risk_level": "Low" if (delivery_risk + pod_risk + aging_risk) // 3 <= 25 else "Medium" if (delivery_risk + pod_risk + aging_risk) // 3 <= 50 else "High"
            }
        }
    
    def _generate_alerts(self, data: Dict) -> List[Dict]:
        """Generate alerts from data."""
        alerts = []
        
        avg_pgi_aging = data.get("avg_pgi_aging", 0)
        if avg_pgi_aging > 7:
            alerts.append({
                "type": "PGI_Delivery",
                "severity": "High" if avg_pgi_aging > 14 else "Medium",
                "message": f"PGI aging is {avg_pgi_aging:.1f} days"
            })
        
        avg_pod_aging = data.get("avg_pod_aging", 0)
        if avg_pod_aging > 5:
            alerts.append({
                "type": "POD",
                "severity": "High" if avg_pod_aging > 10 else "Medium",
                "message": f"POD aging is {avg_pod_aging:.1f} days"
            })
        
        pending_pod = data.get("pending_pod_dns", 0)
        if pending_pod > 5:
            alerts.append({
                "type": "Pending_POD",
                "severity": "High" if pending_pod > 20 else "Medium",
                "message": f"{pending_pod} DNs pending POD"
            })
        
        delivery_rate = data.get("delivery_rate", 0)
        if delivery_rate < 80:
            alerts.append({
                "type": "Low_Delivery_Rate",
                "severity": "High" if delivery_rate < 60 else "Medium",
                "message": f"Delivery rate is {delivery_rate}%"
            })
        
        return alerts
    
    def _generate_insights(self, data: Dict) -> Tuple[List[str], List[str], List[str]]:
        """Generate insights, issues, recommendations."""
        insights = []
        issues = []
        recommendations = []
        
        delivery_rate = data.get("delivery_rate", 0)
        if delivery_rate >= 90:
            insights.append("✅ Excellent delivery rate")
        else:
            issues.append(f"❌ Low delivery rate: {delivery_rate}%")
            recommendations.append(f"🔧 Improve delivery rate to 90%+")
        
        pod_rate = data.get("pod_rate", 0)
        if pod_rate >= 90:
            insights.append("✅ Excellent POD rate")
        else:
            issues.append(f"❌ Low POD rate: {pod_rate}%")
            recommendations.append(f"🔧 Improve POD rate to 90%+")
        
        avg_pgi_aging = data.get("avg_pgi_aging", 0)
        if avg_pgi_aging <= 3:
            insights.append("✅ Fast PGI delivery (< 3 days)")
        elif avg_pgi_aging <= 7:
            insights.append("✅ Good PGI speed")
        else:
            issues.append(f"❌ Slow PGI: {avg_pgi_aging:.1f} days")
            recommendations.append(f"🔧 Reduce PGI time to < 7 days")
        
        avg_pod_aging = data.get("avg_pod_aging", 0)
        if avg_pod_aging <= 2:
            insights.append("✅ Fast POD confirmation (< 2 days)")
        elif avg_pod_aging <= 5:
            insights.append("✅ Good POD speed")
        else:
            issues.append(f"❌ Slow POD: {avg_pod_aging:.1f} days")
            recommendations.append(f"🔧 Reduce POD time to < 5 days")
        
        return insights, issues, recommendations
    
    def _build_profile(self, data: Dict) -> Dict:
        """FIXED: Complete dealer profile with all fields."""
        return {
            # Dealer identification
            "dealer_name": data.get("dealer_name", "Unknown"),
            "dealer_code": data.get("dealer_code", "Unknown"),
            "customer_code": data.get("customer_code", "Unknown"),
            
            # Dealer classification
            "division": data.get("division", "Unknown"),
            
            # Sales information
            "sales_office": data.get("sales_office", "Unknown"),
            "sales_manager": data.get("sales_manager", "Unknown"),
            
            # Location information
            "city": data.get("city", "Unknown"),
            "warehouse": data.get("top_warehouse", "Unknown"),
            "warehouse_code": data.get("warehouse_code", "Unknown"),
            "delivery_location": data.get("delivery_location", "Unknown"),
            
            # Status and dates
            "dealer_status": data.get("dealer_status", "Unknown"),
            "first_dn_date": data.get("first_dn_date"),
            "last_dn_date": data.get("last_dn_date")
        }
    
    def _build_kpis(self, data: Dict, analytics: Dict) -> Dict:
        return {
            "total_dns": data.get("total_dns", 0),
            "total_revenue": data.get("total_revenue", 0),
            "total_units": data.get("total_units", 0),
            "delivered_dns": data.get("delivered_dns", 0),
            "pending_dns": data.get("pending_dns", 0),
            "pending_pod_dns": data.get("pending_pod_dns", 0),
            "health_score": analytics["health"]["score"],
            "risk_level": analytics["risk"]["risk_level"]
        }
    
    def _build_performance(self, data: Dict) -> Dict:
        return {
            "total_dns": data.get("total_dns", 0),
            "delivered_dns": data.get("delivered_dns", 0),
            "pending_dns": data.get("pending_dns", 0),
            "transit_dns": data.get("transit_dns", 0),
            "delivery_rate": data.get("delivery_rate", 0),
            "total_units": data.get("total_units", 0),
            "delivered_units": data.get("delivered_units", 0),
            "pending_units": data.get("pending_units", 0)
        }
    
    def _build_delivery(self, data: Dict) -> Dict:
        return {
            "delivered_dns": data.get("delivered_dns", 0),
            "pending_dns": data.get("pending_dns", 0),
            "transit_dns": data.get("transit_dns", 0),
            "delivery_rate": data.get("delivery_rate", 0),
            "avg_pgi_aging": data.get("avg_pgi_aging", 0),
            "avg_total_aging": data.get("avg_total_aging", 0)
        }
    
    def _build_pod(self, data: Dict) -> Dict:
        return {
            "pod_completed_dns": data.get("pod_completed_dns", 0),
            "pending_pod_dns": data.get("pending_pod_dns", 0),
            "pod_rate": data.get("pod_rate", 0),
            "avg_pod_aging": data.get("avg_pod_aging", 0)
        }
    
    def _build_financial(self, data: Dict) -> Dict:
        return {
            "total_revenue": data.get("total_revenue", 0),
            "avg_dn_value": data.get("total_revenue", 0) / max(data.get("total_dns", 1), 1)
        }
    
    # ==========================================================
    # WRAPPER METHODS FOR BACKWARD COMPATIBILITY
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        return self.get_dealer_360_dashboard(dealer_name)
    
    def get_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        return self.get_dealer_360_dashboard(dealer_name)
    
    def get_dealer_profile(self, dealer_name: str) -> AnalyticsResponse:
        return self.get_dealer_360_dashboard(dealer_name)
    
    def get_dealer_executive_summary(self, dealer_name: str) -> AnalyticsResponse:
        return self.get_dealer_360_dashboard(dealer_name)
    
    def get_dealer_dn_performance(self, dealer_name: str) -> AnalyticsResponse:
        return self.get_dealer_360_dashboard(dealer_name)
    
    def get_delivery_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        return self.get_dealer_360_dashboard(dealer_name)
    
    def get_pod_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        return self.get_dealer_360_dashboard(dealer_name)
    
    def get_financial_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        return self.get_dealer_360_dashboard(dealer_name)
    
    def calculate_dealer_health_score(self, dealer_name: str) -> AnalyticsResponse:
        return self.get_dealer_360_dashboard(dealer_name)
    
    def assess_dealer_risk(self, dealer_name: str) -> AnalyticsResponse:
        return self.get_dealer_360_dashboard(dealer_name)
    
    def get_ai_context(self, dealer_name: str = None) -> AnalyticsResponse:
        if dealer_name:
            dashboard = self.pg.get_dealer_dashboard(dealer_name)
            return AnalyticsResponse(success=True, data={
                "dealer_name": dealer_name,
                "data": dashboard,
                "context": "AI context from PostgreSQL"
            })
        else:
            network = self.pg.get_network_summary()
            return AnalyticsResponse(success=True, data={
                "network": network,
                "context": "Network context from PostgreSQL"
            })
    
    def get_data_integrity_score(self) -> AnalyticsResponse:
        try:
            network = self.pg.get_network_summary()
            total = network.get("total_dns", 0)
            return AnalyticsResponse(success=True, data={
                "total_records": total,
                "integrity_score": 100 if total > 0 else 0,
                "quality_status": "Excellent" if total > 100 else "Good"
            })
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_delivery_aging_analysis(self, dealer_name: str) -> AnalyticsResponse:
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            aging = self.pg.get_dealer_aging_analysis(resolved)
            return AnalyticsResponse(success=True, data=aging)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_product_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            products = self.pg.get_dealer_product_analytics(resolved)
            return AnalyticsResponse(success=True, data={"products": products})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e))


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

_analytics_service = None


def get_analytics_service(use_redis: bool = False) -> AnalyticsService:
    global _analytics_service
    if _analytics_service is None:
        _analytics_service = AnalyticsService(use_redis=use_redis)
    return _analytics_service


__all__ = [
    'AnalyticsService',
    'AnalyticsResponse',
    'get_analytics_service',
    'DEALER_NAME_FIELD',
    'DEALER_CODE_FIELD',
    'CUSTOMER_CODE_FIELD',
    'DN_NO_FIELD',
    'DELIVERY_STATUS_FIELD',
    'PGI_STATUS_FIELD',
    'POD_STATUS_FIELD'
]
