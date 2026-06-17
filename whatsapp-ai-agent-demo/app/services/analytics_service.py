# ==========================================================
# FILE: app/services/analytics_service.py (v8.1 - FULLY ALIGNED)
# ==========================================================
# PURPOSE: Business Intelligence Layer - Enterprise Dealer Intelligence Engine
# VERSION: 8.1 - Complete Alignment with ai_provider_service.py
#
# CRITICAL FIXES:
# 1. ✅ get_dn_analytics() ALWAYS returns AnalyticsResponse
# 2. ✅ Production diagnostics with detailed logging
# 3. ✅ verify_dn_exists() - DN verification method
# 4. ✅ debug_dn() - DN debug method
# 5. ✅ debug_dealer() - Dealer debug method
# 6. ✅ debug_database() - Database health check
# 7. ✅ Dashboard response ALWAYS contains: summary, aging, performance, profile
# 8. ✅ Null safety for all SQL outputs (or 0)
# 9. ✅ DN normalization aligned with ai_provider_service (re.sub(r"\D", "", str(dn)))
# 10. ✅ All methods return AnalyticsResponse with proper structure
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
import uuid
import re
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

DEALER_NAME_FIELD = "customer_name"
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
        return bool(cls.DATABASE_URL)


# ==========================================================
# ENTERPRISE EXCEPTION HIERARCHY
# ==========================================================

class AnalyticsError(Exception):
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
# POSTGRESQL QUERY ENGINE
# ==========================================================

class PostgreSQLQueryEngine:
    def __init__(self, logistics_service):
        self.logistics = logistics_service
        self.db = logistics_service.db
    
    def execute(self, sql: str, params: Dict = None) -> List[Dict]:
        try:
            result = self.db.execute(text(sql), params or {})
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
        results = self.execute(sql, params)
        return results[0] if results else None
    
    # ==========================================================
    # CORE QUERIES
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> Optional[Dict]:
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
                    END) as avg_total_aging,
                    
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
                ROUND((delivered_dns::float / NULLIF(total_dns, 0)) * 100, 1) as delivery_rate,
                ROUND((pod_completed_dns::float / NULLIF(delivered_dns, 0)) * 100, 1) as pod_rate,
                CASE 
                    WHEN delivered_dns = 0 AND total_dns = 0 THEN 'Inactive'
                    WHEN total_dns < 10 THEN 'Low Activity'
                    WHEN (delivered_dns::float / NULLIF(total_dns, 0)) >= 0.9 THEN 'Active - High Performance'
                    ELSE 'Active - Needs Attention'
                END as dealer_status,
                
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
        if not dealer_input or not dealer_input.strip():
            return None
        
        dealer_input = dealer_input.strip()
        
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
# ANALYTICS SERVICE - FULLY ALIGNED v8.1
# ==========================================================

class AnalyticsService:
    """
    ENTERPRISE DEALER INTELLIGENCE ENGINE v8.1
    Fully aligned with ai_provider_service.py
    """
    
    DEALER_NAME_FIELD = DEALER_NAME_FIELD
    
    def __init__(self, use_redis: bool = False):
        self._start_time = time.time()
        
        self.is_railway = RailwayPostgresConfig.is_railway()
        if self.is_railway:
            logger.info("🚆 Running on Railway - 100% PostgreSQL mode enabled")
        
        self.logistics = LogisticsQueryService()
        self.kpi = KPIService()
        self.schema = get_schema_service()
        self.today = datetime.now().date()
        
        self.pg = PostgreSQLQueryEngine(self.logistics)
        
        self._cache: Dict[str, Any] = {}
        self._cache_ttl: Dict[str, datetime] = {}
        self._dealer_cache: Dict[str, Tuple[str, datetime]] = {}
        
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
            "errors_by_type": defaultdict(int),
            "dn_lookups": 0,
            "dn_lookups_success": 0,
            "dn_lookups_failure": 0
        }
        
        self._test_postgresql()
        
        logger.info("=" * 70)
        logger.info("AnalyticsService v8.1 - Fully Aligned")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   ✅ CRITICAL FIXES:")
        logger.info("      - get_dn_analytics() ALWAYS returns AnalyticsResponse")
        logger.info("      - Production diagnostics with detailed logging")
        logger.info("      - verify_dn_exists() - DN verification")
        logger.info("      - debug_dn() - DN debug method")
        logger.info("      - debug_dealer() - Dealer debug method")
        logger.info("      - debug_database() - Database health check")
        logger.info("      - Dashboard response contains summary, aging, performance, profile")
        logger.info("      - Null safety for all SQL outputs (or 0)")
        logger.info("      - DN normalization aligned (re.sub(r'\\D', '', str(dn)))")
        logger.info("")
        logger.info(f"   🌐 Environment: {'Railway' if self.is_railway else 'Local'}")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 70)
    
    def _test_postgresql(self):
        try:
            result = self.logistics.db.execute(text("SELECT version()"))
            version = result.first()[0]
            logger.info(f"✅ PostgreSQL connected: {version[:50]}...")
            
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
    # DEALER RESOLUTION
    # ==========================================================
    
    def _resolve_dealer(self, dealer_input: str) -> Optional[str]:
        if not dealer_input:
            return None
        
        cached = self._get_cached_dealer(dealer_input)
        if cached:
            return cached
        
        resolved = self.pg.resolve_dealer_postgres(dealer_input)
        if resolved:
            self._set_cached_dealer(dealer_input, resolved)
            self.metrics["dealer_resolution_success"] += 1
        else:
            self.metrics["dealer_resolution_failure"] += 1
        
        return resolved
    
    # ==========================================================
    # DN NORMALIZATION (ALIGNED WITH ai_provider_service)
    # ==========================================================
    
    def _normalize_dn(self, dn: str) -> Optional[str]:
        """
        Normalize DN by removing all non-digit characters.
        ALIGNED with ai_provider_service.py normalization.
        
        Examples:
        - "6243611858" → "6243611858"
        - "6243611858." → "6243611858"
        - "DN 6243611858" → "6243611858"
        - "6243611858-0" → "6243611858"
        """
        if not dn:
            return None
        
        # Remove all non-digit characters (identical to ai_provider_service)
        normalized = re.sub(r'\D', '', str(dn).strip())
        
        # Validate length (8-12 digits)
        if len(normalized) < 8 or len(normalized) > 12:
            return None
        
        return normalized
    
    # ==========================================================
    # VERIFY DN EXISTS (NEW DIAGNOSTIC METHOD)
    # ==========================================================
    
    def verify_dn_exists(self, dn_no: str) -> Dict[str, Any]:
        """
        Direct verification if a DN exists in the database.
        
        Returns:
            {
                "dn": "6243610989",
                "found": True,
                "record": {
                    "dn_no": "6243610989",
                    "customer_name": "Gul Electronics Shinkiari",
                    "dealer_code": "ABC123"
                }
            }
        """
        request_id = str(uuid.uuid4())[:8]
        
        try:
            logger.info(f"[{request_id}] 🔍 VERIFY_DN: {dn_no}")
            
            # Normalize the DN
            normalized = self._normalize_dn(dn_no)
            
            if not normalized:
                logger.warning(f"[{request_id}] Invalid DN format: {dn_no}")
                return {
                    "dn": dn_no,
                    "normalized": None,
                    "found": False,
                    "error": "Invalid DN format"
                }
            
            # Query the database
            record = self.pg.execute_one(
                """
                SELECT
                    dn_no,
                    customer_name,
                    dealer_code,
                    customer_code,
                    warehouse,
                    ship_to_city
                FROM delivery_reports
                WHERE CAST(dn_no AS TEXT) = :dn_no
                LIMIT 1
                """,
                {"dn_no": normalized}
            )
            
            found = record is not None
            
            logger.info(f"[{request_id}] DN_FOUND={found}")
            
            return {
                "dn": dn_no,
                "normalized": normalized,
                "found": found,
                "record": record if record else None
            }
            
        except Exception as e:
            logger.error(f"[{request_id}] Verify DN failed: {e}")
            return {
                "dn": dn_no,
                "found": False,
                "error": str(e)
            }
    
    # ==========================================================
    # DEBUG DN (NEW DIAGNOSTIC METHOD)
    # ==========================================================
    
    def debug_dn(self, dn_no: str) -> Dict[str, Any]:
        """
        Debug a DN with detailed information.
        
        Returns:
            {
                "input": "6243610989",
                "normalized": "6243610989",
                "exists": {
                    "found": True,
                    "record": {...}
                },
                "validation": {
                    "is_valid": True,
                    "issues": [],
                    "durations": {...}
                }
            }
        """
        request_id = str(uuid.uuid4())[:8]
        
        try:
            logger.info(f"[{request_id}] 🔍 DEBUG_DN: {dn_no}")
            
            normalized = self._normalize_dn(dn_no)
            exists_result = self.verify_dn_exists(dn_no)
            
            # If found, get additional details
            validation = {}
            if exists_result.get("found"):
                # Get full record
                record = self.logistics.get_dn_details(normalized)
                if record:
                    validation = self._calculate_dn_metrics(record)
            
            return {
                "input": dn_no,
                "normalized": normalized,
                "exists": exists_result,
                "validation": validation,
                "request_id": request_id
            }
            
        except Exception as e:
            logger.error(f"[{request_id}] Debug DN failed: {e}")
            return {
                "input": dn_no,
                "error": str(e)
            }
    
    # ==========================================================
    # DEBUG DEALER (NEW DIAGNOSTIC METHOD)
    # ==========================================================
    
    def debug_dealer(self, dealer_name: str) -> Dict[str, Any]:
        """
        Debug a dealer with detailed resolution information.
        
        Returns:
            {
                "input": "Gul Electronics Shinkiari",
                "resolved": True,
                "resolved_name": "Gul Electronics Shinkiari",
                "total_dns": 45,
                "profile": {...}
            }
        """
        request_id = str(uuid.uuid4())[:8]
        
        try:
            logger.info(f"[{request_id}] 🔍 DEBUG_DEALER: {dealer_name}")
            
            # Try to resolve the dealer
            resolved = self._resolve_dealer(dealer_name)
            
            if not resolved:
                logger.warning(f"[{request_id}] Dealer not resolved: {dealer_name}")
                return {
                    "input": dealer_name,
                    "resolved": False,
                    "request_id": request_id
                }
            
            # Get dashboard data
            dashboard = self.pg.get_dealer_dashboard(resolved)
            
            if not dashboard:
                logger.warning(f"[{request_id}] No data for dealer: {resolved}")
                return {
                    "input": dealer_name,
                    "resolved": True,
                    "resolved_name": resolved,
                    "total_dns": 0,
                    "request_id": request_id
                }
            
            # Build response with null safety
            total_dns = dashboard.get("total_dns") or 0
            
            return {
                "input": dealer_name,
                "resolved": True,
                "resolved_name": resolved,
                "total_dns": total_dns,
                "profile": self._build_profile(dashboard),
                "summary": {
                    "total_units": dashboard.get("total_units") or 0,
                    "total_revenue": dashboard.get("total_revenue") or 0,
                    "delivered_dns": dashboard.get("delivered_dns") or 0,
                    "pending_dns": dashboard.get("pending_dns") or 0
                },
                "request_id": request_id
            }
            
        except Exception as e:
            logger.error(f"[{request_id}] Debug dealer failed: {e}")
            return {
                "input": dealer_name,
                "resolved": False,
                "error": str(e)
            }
    
    # ==========================================================
    # DEBUG DATABASE (NEW DIAGNOSTIC METHOD)
    # ==========================================================
    
    def debug_database(self) -> Dict[str, Any]:
        """
        Debug database health and connection.
        
        Returns:
            {
                "connected": True,
                "table_name": "delivery_reports",
                "total_records": 125432,
                "total_dns": 89211,
                "total_dealers": 245,
                "sample_dn": "6243610989"
            }
        """
        request_id = str(uuid.uuid4())[:8]
        
        try:
            logger.info(f"[{request_id}] 🔍 DEBUG_DATABASE")
            
            # Check connection
            connection_result = self.pg.execute_one("SELECT 1 as connected")
            connected = connection_result is not None
            
            if not connected:
                return {
                    "connected": False,
                    "error": "Database connection failed"
                }
            
            # Get counts
            count_result = self.pg.execute_one(
                f"SELECT COUNT(*) as total FROM {self.pg.logistics.table_name}"
            )
            total_records = count_result.get("total") if count_result else 0
            
            dn_count_result = self.pg.execute_one(
                f"SELECT COUNT(DISTINCT dn_no) as total FROM {self.pg.logistics.table_name}"
            )
            total_dns = dn_count_result.get("total") if dn_count_result else 0
            
            dealer_count_result = self.pg.execute_one(
                f"SELECT COUNT(DISTINCT customer_name) as total FROM {self.pg.logistics.table_name} WHERE customer_name IS NOT NULL"
            )
            total_dealers = dealer_count_result.get("total") if dealer_count_result else 0
            
            # Get sample DN
            sample_result = self.pg.execute_one(
                f"SELECT dn_no FROM {self.pg.logistics.table_name} LIMIT 1"
            )
            sample_dn = sample_result.get("dn_no") if sample_result else None
            
            return {
                "connected": True,
                "table_name": self.pg.logistics.table_name,
                "total_records": total_records,
                "total_dns": total_dns,
                "total_dealers": total_dealers,
                "sample_dn": sample_dn,
                "request_id": request_id
            }
            
        except Exception as e:
            logger.error(f"[{request_id}] Debug database failed: {e}")
            return {
                "connected": False,
                "error": str(e)
            }
    
    # ==========================================================
    # DN ANALYTICS (ALWAYS RETURNS AnalyticsResponse)
    # ==========================================================
    
    def get_dn_analytics(self, dn_number: str) -> AnalyticsResponse:
        """
        Get comprehensive DN analytics.
        
        ALWAYS RETURNS AnalyticsResponse with:
        - success: bool
        - data: Dict with record, validation, status
        - error: Optional[str]
        - error_id: Optional[str]
        """
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        self.metrics["total_requests"] += 1
        self.metrics["dn_lookups"] += 1
        
        # PRODUCTION DIAGNOSTICS
        logger.info(f"[{request_id}] DN_LOOKUP_START={dn_number}")
        
        try:
            # Validate input
            if not dn_number or not dn_number.strip():
                error_id = str(uuid.uuid4())[:8]
                self.metrics["failed_requests"] += 1
                self.metrics["dn_lookups_failure"] += 1
                logger.warning(f"[{request_id}] DN_LOOKUP_FAILED=empty_input")
                return AnalyticsResponse(
                    success=False,
                    error="DN number cannot be empty",
                    error_id=error_id
                )
            
            # Normalize DN (aligned with ai_provider_service)
            normalized_dn = self._normalize_dn(dn_number)
            
            # PRODUCTION DIAGNOSTICS
            logger.info(f"[{request_id}] DN_NORMALIZED={normalized_dn}")
            
            if not normalized_dn:
                error_id = str(uuid.uuid4())[:8]
                self.metrics["failed_requests"] += 1
                self.metrics["dn_lookups_failure"] += 1
                logger.warning(f"[{request_id}] DN_LOOKUP_FAILED=invalid_format")
                return AnalyticsResponse(
                    success=False,
                    error=f"Invalid DN format: {dn_number}. DN must be 8-12 digits.",
                    error_id=error_id
                )
            
            # Get DN details from logistics service
            record = self.logistics.get_dn_details(normalized_dn)
            
            # PRODUCTION DIAGNOSTICS
            logger.info(f"[{request_id}] DN_ROWS_FOUND={1 if record else 0}")
            
            if not record:
                self.metrics["failed_requests"] += 1
                self.metrics["dn_lookups_failure"] += 1
                logger.warning(f"[{request_id}] DN_LOOKUP_FAILED=not_found")
                return AnalyticsResponse(
                    success=False,
                    error=f"DN {dn_number} not found in database",
                    error_id=str(uuid.uuid4())[:8]
                )
            
            # Calculate metrics and validation
            metrics = self._calculate_dn_metrics(record)
            
            # Build response data
            data = {
                "record": record,
                "validation": metrics.get("validation", {}),
                "status": metrics.get("status", "unknown"),
                "found": True,
                "request_id": request_id,
                "duration_ms": (time.time() - start_time) * 1000
            }
            
            duration_ms = (time.time() - start_time) * 1000
            self.metrics["successful_requests"] += 1
            self.metrics["dn_lookups_success"] += 1
            self.metrics["total_duration_ms"] += duration_ms
            
            # PRODUCTION DIAGNOSTICS
            logger.info(f"[{request_id}] DN_SUCCESS=True")
            logger.info(f"[{request_id}] DN_LOOKUP_DURATION={duration_ms:.2f}ms")
            
            return AnalyticsResponse(success=True, data=data)
            
        except Exception as e:
            error_id = str(uuid.uuid4())[:8]
            self.metrics["failed_requests"] += 1
            self.metrics["dn_lookups_failure"] += 1
            self.metrics["errors_by_type"][type(e).__name__] += 1
            
            # PRODUCTION DIAGNOSTICS
            logger.error(f"[{request_id}] DN_LOOKUP_FAILED=exception")
            logger.exception(f"[{request_id}] DN Analytics error: {e}")
            
            return AnalyticsResponse(
                success=False,
                error=str(e),
                error_id=error_id
            )
    
    # ==========================================================
    # DN METRICS HELPER
    # ==========================================================
    
    def _calculate_dn_metrics(self, record: Dict) -> Dict[str, Any]:
        """
        Calculate DN metrics including validation, status, and durations.
        """
        validation = {
            "is_valid": True,
            "issues": [],
            "warnings": [],
            "durations": {},
            "data_quality_flags": {}
        }
        
        # Extract dates
        dn_date = record.get('dn_date') or record.get('dn_create_date')
        pgi_date = record.get('pgi_date') or record.get('good_issue_date')
        pod_date = record.get('pod_date')
        
        # Check missing dates
        missing_dates = []
        if dn_date is None:
            missing_dates.append("DN Create Date")
            validation["is_valid"] = False
        if pgi_date is None:
            missing_dates.append("PGI Date")
        if pod_date is None:
            missing_dates.append("POD Date")
        
        if missing_dates:
            validation["issues"].append(f"Missing dates: {', '.join(missing_dates)}")
        
        # Calculate durations
        if dn_date and pgi_date:
            processing_days = (pgi_date - dn_date).days
            if processing_days < 0:
                validation["issues"].append(f"⚠️ Negative processing time: {processing_days} days")
                validation["is_valid"] = False
                validation["durations"]["processing_time_days"] = None
            else:
                validation["durations"]["processing_time_days"] = processing_days
        else:
            validation["durations"]["processing_time_days"] = None
        
        if pgi_date and pod_date:
            delivery_days = (pod_date - pgi_date).days
            if delivery_days < 0:
                validation["issues"].append(f"⚠️ Negative delivery time: {delivery_days} days")
                validation["is_valid"] = False
                validation["durations"]["delivery_time_days"] = None
            else:
                validation["durations"]["delivery_time_days"] = delivery_days
        else:
            validation["durations"]["delivery_time_days"] = None
        
        if dn_date and pod_date:
            cycle_days = (pod_date - dn_date).days
            if cycle_days < 0:
                validation["issues"].append(f"⚠️ Negative cycle time: {cycle_days} days")
                validation["is_valid"] = False
                validation["durations"]["total_cycle_days"] = None
            else:
                validation["durations"]["total_cycle_days"] = cycle_days
        else:
            validation["durations"]["total_cycle_days"] = None
        
        # Check date sequence
        if dn_date and pgi_date and pod_date:
            if pgi_date < dn_date:
                validation["issues"].append(
                    f"⚠️ Data Integrity Issue: PGI Date ({pgi_date.strftime('%Y-%m-%d')}) "
                    f"occurs before DN Date ({dn_date.strftime('%Y-%m-%d')})"
                )
                validation["is_valid"] = False
            
            if pod_date < pgi_date:
                validation["issues"].append(
                    f"⚠️ Data Integrity Issue: POD Date ({pod_date.strftime('%Y-%m-%d')}) "
                    f"occurs before PGI Date ({pgi_date.strftime('%Y-%m-%d')})"
                )
                validation["is_valid"] = False
            
            if pod_date < dn_date:
                validation["issues"].append(
                    f"⚠️ Data Integrity Issue: POD Date ({pod_date.strftime('%Y-%m-%d')}) "
                    f"occurs before DN Date ({dn_date.strftime('%Y-%m-%d')})"
                )
                validation["is_valid"] = False
        
        # Data quality flags
        validation["data_quality_flags"] = {
            'missing_dn_date': dn_date is None,
            'missing_pgi_date': pgi_date is None,
            'missing_pod_date': pod_date is None,
            'invalid_date_sequence': not validation["is_valid"] if (dn_date and pgi_date and pod_date) else False
        }
        
        # Determine status
        status = "unknown"
        if pod_date:
            status = "delivered"
        elif pgi_date:
            status = "pending_pod"
        elif dn_date:
            status = "pending_pgi"
        
        return {
            "validation": validation,
            "status": status
        }
    
    # ==========================================================
    # DEALER 360 DASHBOARD (WITH ALL REQUIRED KEYS)
    # ==========================================================
    
    def get_dealer_360_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get complete dealer dashboard from PostgreSQL.
        
        ALWAYS RETURNS AnalyticsResponse with data containing:
        - summary: Dict
        - aging: Dict
        - performance: Dict
        - profile: Dict
        """
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
            
            # Build dashboard with ALL required keys
            analytics = self._calculate_analytics_from_data(dashboard_data)
            
            # IMPORTANT: Ensure all required keys exist with null safety
            summary = {
                "total_dns": dashboard_data.get("total_dns") or 0,
                "total_units": dashboard_data.get("total_units") or 0,
                "total_revenue": dashboard_data.get("total_revenue") or 0,
                "delivered": dashboard_data.get("delivered_dns") or 0,
                "in_transit": dashboard_data.get("transit_dns") or 0,
                "delivery_rate": dashboard_data.get("delivery_rate") or 0,
                "pod_rate": dashboard_data.get("pod_rate") or 0
            }
            
            aging = {
                "pending_pgi": dashboard_data.get("pending_dns") or 0,
                "pending_pod": dashboard_data.get("pending_pod_dns") or 0,
                "avg_delivery_aging": dashboard_data.get("avg_pgi_aging") or 0,
                "avg_pod_aging": dashboard_data.get("avg_pod_aging") or 0,
                "avg_total_aging": dashboard_data.get("avg_total_aging") or 0
            }
            
            performance = {
                "risk_status": analytics["risk"]["risk_level"].lower() if analytics.get("risk") else "low",
                "health_score": analytics["health"]["score"] if analytics.get("health") else 0
            }
            
            profile = self._build_profile(dashboard_data)
            
            # Build complete dashboard
            dashboard = {
                "success": True,
                "request_id": request_id,
                "dealer_name": resolved,
                "profile": profile,
                "summary": summary,
                "aging": aging,
                "performance": performance,
                "executive_kpis": self._build_kpis(dashboard_data, analytics),
                "delivery": self._build_delivery(dashboard_data),
                "pod": self._build_pod(dashboard_data),
                "financial": self._build_financial(dashboard_data),
                "health": analytics["health"] if analytics.get("health") else {"score": 0, "category": "Unknown"},
                "risk": analytics["risk"] if analytics.get("risk") else {"risk_score": 0, "risk_level": "Unknown"},
                "trends": self.pg.get_dealer_trends(resolved),
                "products": self.pg.get_dealer_product_analytics(resolved),
                "location": self.pg.get_dealer_location_analytics(resolved),
                "aging_data": self.pg.get_dealer_aging_analysis(resolved),
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
    # ALL DEALERS DASHBOARD
    # ==========================================================
    
    def get_all_dealers_dashboard(self) -> AnalyticsResponse:
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
    # DEALER RANKINGS
    # ==========================================================
    
    def get_dealer_rankings(self, dealer_name: str) -> AnalyticsResponse:
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
    # DEALER TIMELINE
    # ==========================================================
    
    def get_dealer_timeline(self, dealer_name: str, limit: int = 20) -> AnalyticsResponse:
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
    # DEALER ALERTS
    # ==========================================================
    
    def get_dealer_alerts(self, dealer_name: str) -> AnalyticsResponse:
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
    # EXECUTIVE INSIGHTS
    # ==========================================================
    
    def get_executive_insights(self, dealer_name: str = None) -> AnalyticsResponse:
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
    # HEALTH CHECK
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        status = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": "8.1",
            "environment": "Railway" if self.is_railway else "Local",
            "checks": {}
        }
        
        try:
            result = self.logistics.db.execute(text("SELECT 1 as connected"))
            if result.first():
                status["checks"]["postgresql"] = {"status": "healthy", "message": "Connected"}
            else:
                status["checks"]["postgresql"] = {"status": "warning", "message": "Connection issue"}
        except Exception as e:
            status["status"] = "unhealthy"
            status["checks"]["postgresql"] = {"status": "unhealthy", "message": str(e)}
        
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
            "dn_lookups": self.metrics["dn_lookups"],
            "dn_lookups_success": self.metrics["dn_lookups_success"],
            "dn_lookups_failure": self.metrics["dn_lookups_failure"],
            "dn_lookups_success_rate": round((self.metrics["dn_lookups_success"] / max(self.metrics["dn_lookups"], 1)) * 100, 1),
            "version": "8.1",
            "environment": "Railway" if self.is_railway else "Local"
        }
    
    # ==========================================================
    # PRIVATE HELPERS
    # ==========================================================
    
    def _calculate_analytics_from_data(self, data: Dict) -> Dict:
        delivery_rate = data.get("delivery_rate") or 0
        pod_rate = data.get("pod_rate") or 0
        avg_pgi_aging = data.get("avg_pgi_aging") or 0
        avg_total_aging = data.get("avg_total_aging") or 0
        revenue = data.get("total_revenue") or 0
        total_dns = data.get("total_dns") or 0
        
        health_score = int(
            (min(delivery_rate / 90 * 100, 100) * 0.40) +
            (min(pod_rate / 90 * 100, 100) * 0.30) +
            (max(100 - min(avg_total_aging / 30 * 100, 100), 0) * 0.20) +
            (min(revenue / 1000000 * 100, 100) * 0.10)
        )
        
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
        alerts = []
        
        avg_pgi_aging = data.get("avg_pgi_aging") or 0
        if avg_pgi_aging > 7:
            alerts.append({
                "type": "PGI_Delivery",
                "severity": "High" if avg_pgi_aging > 14 else "Medium",
                "message": f"PGI aging is {avg_pgi_aging:.1f} days"
            })
        
        avg_pod_aging = data.get("avg_pod_aging") or 0
        if avg_pod_aging > 5:
            alerts.append({
                "type": "POD",
                "severity": "High" if avg_pod_aging > 10 else "Medium",
                "message": f"POD aging is {avg_pod_aging:.1f} days"
            })
        
        pending_pod = data.get("pending_pod_dns") or 0
        if pending_pod > 5:
            alerts.append({
                "type": "Pending_POD",
                "severity": "High" if pending_pod > 20 else "Medium",
                "message": f"{pending_pod} DNs pending POD"
            })
        
        delivery_rate = data.get("delivery_rate") or 0
        if delivery_rate < 80:
            alerts.append({
                "type": "Low_Delivery_Rate",
                "severity": "High" if delivery_rate < 60 else "Medium",
                "message": f"Delivery rate is {delivery_rate}%"
            })
        
        return alerts
    
    def _generate_insights(self, data: Dict) -> Tuple[List[str], List[str], List[str]]:
        insights = []
        issues = []
        recommendations = []
        
        delivery_rate = data.get("delivery_rate") or 0
        if delivery_rate >= 90:
            insights.append("✅ Excellent delivery rate")
        else:
            issues.append(f"❌ Low delivery rate: {delivery_rate}%")
            recommendations.append(f"🔧 Improve delivery rate to 90%+")
        
        pod_rate = data.get("pod_rate") or 0
        if pod_rate >= 90:
            insights.append("✅ Excellent POD rate")
        else:
            issues.append(f"❌ Low POD rate: {pod_rate}%")
            recommendations.append(f"🔧 Improve POD rate to 90%+")
        
        avg_pgi_aging = data.get("avg_pgi_aging") or 0
        if avg_pgi_aging <= 3:
            insights.append("✅ Fast PGI delivery (< 3 days)")
        elif avg_pgi_aging <= 7:
            insights.append("✅ Good PGI speed")
        else:
            issues.append(f"❌ Slow PGI: {avg_pgi_aging:.1f} days")
            recommendations.append(f"🔧 Reduce PGI time to < 7 days")
        
        avg_pod_aging = data.get("avg_pod_aging") or 0
        if avg_pod_aging <= 2:
            insights.append("✅ Fast POD confirmation (< 2 days)")
        elif avg_pod_aging <= 5:
            insights.append("✅ Good POD speed")
        else:
            issues.append(f"❌ Slow POD: {avg_pod_aging:.1f} days")
            recommendations.append(f"🔧 Reduce POD time to < 5 days")
        
        return insights, issues, recommendations
    
    def _build_profile(self, data: Dict) -> Dict:
        """Complete dealer profile with all fields."""
        return {
            "dealer_name": data.get("dealer_name", "Unknown"),
            "dealer_code": data.get("dealer_code", "Unknown"),
            "customer_code": data.get("customer_code", "Unknown"),
            "division": data.get("division", "Unknown"),
            "sales_office": data.get("sales_office", "Unknown"),
            "sales_manager": data.get("sales_manager", "Unknown"),
            "city": data.get("city", "Unknown"),
            "warehouse": data.get("top_warehouse", "Unknown"),
            "warehouse_code": data.get("warehouse_code", "Unknown"),
            "delivery_location": data.get("delivery_location", "Unknown"),
            "dealer_status": data.get("dealer_status", "Unknown"),
            "first_dn_date": data.get("first_dn_date"),
            "last_dn_date": data.get("last_dn_date")
        }
    
    def _build_kpis(self, data: Dict, analytics: Dict) -> Dict:
        return {
            "total_dns": data.get("total_dns") or 0,
            "total_revenue": data.get("total_revenue") or 0,
            "total_units": data.get("total_units") or 0,
            "delivered_dns": data.get("delivered_dns") or 0,
            "pending_dns": data.get("pending_dns") or 0,
            "pending_pod_dns": data.get("pending_pod_dns") or 0,
            "health_score": analytics["health"]["score"] if analytics.get("health") else 0,
            "risk_level": analytics["risk"]["risk_level"] if analytics.get("risk") else "Unknown"
        }
    
    def _build_delivery(self, data: Dict) -> Dict:
        return {
            "delivered_dns": data.get("delivered_dns") or 0,
            "pending_dns": data.get("pending_dns") or 0,
            "transit_dns": data.get("transit_dns") or 0,
            "delivery_rate": data.get("delivery_rate") or 0,
            "avg_pgi_aging": data.get("avg_pgi_aging") or 0,
            "avg_total_aging": data.get("avg_total_aging") or 0
        }
    
    def _build_pod(self, data: Dict) -> Dict:
        return {
            "pod_completed_dns": data.get("pod_completed_dns") or 0,
            "pending_pod_dns": data.get("pending_pod_dns") or 0,
            "pod_rate": data.get("pod_rate") or 0,
            "avg_pod_aging": data.get("avg_pod_aging") or 0
        }
    
    def _build_financial(self, data: Dict) -> Dict:
        total_dns = data.get("total_dns") or 1
        return {
            "total_revenue": data.get("total_revenue") or 0,
            "avg_dn_value": (data.get("total_revenue") or 0) / total_dns
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
            total = network.get("total_dns") or 0
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
