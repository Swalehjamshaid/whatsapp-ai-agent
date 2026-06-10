# ==========================================================
# FILE: app/services/analytics_service.py
# VERSION: 3.0
# PURPOSE: Performance Analytics Engine - Rankings, Trends, Comparisons, Growth Analysis
# ARCHITECTURE: ai_query_service → analytics_service
# ==========================================================

import json
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta, date
from collections import defaultdict, Counter
from sqlalchemy import text, func, and_, or_
from sqlalchemy.orm import Session
from loguru import logger

# ==========================================================
# ANALYTICS QUERIES
# ==========================================================

class AnalyticsQueries:
    """Container for analytics SQL queries"""
    
    # Top Dealers by Value
    TOP_DEALERS_BY_VALUE = """
        SELECT 
            dn.dealer_code,
            dn.dealer_name,
            dn.dealer_city,
            dn.dealer_region,
            COUNT(*) as total_dns,
            SUM(dn.amount) as total_value,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN dn.amount ELSE 0 END) as completed_value,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as completed_dns,
            AVG(CASE WHEN dn.pod_date IS NOT NULL 
                THEN DATEDIFF(dn.pod_date, dn.shipment_date) 
                ELSE NULL END) as avg_delivery_days,
            RANK() OVER (ORDER BY SUM(dn.amount) DESC) as value_rank
        FROM dn_master dn
        WHERE dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        {region_filter}
        {city_filter}
        GROUP BY dn.dealer_code, dn.dealer_name, dn.dealer_city, dn.dealer_region
        ORDER BY total_value DESC
        LIMIT :limit
    """
    
    # Top Dealers by Volume
    TOP_DEALERS_BY_VOLUME = """
        SELECT 
            dn.dealer_code,
            dn.dealer_name,
            dn.dealer_city,
            dn.dealer_region,
            COUNT(*) as total_dns,
            SUM(dn.amount) as total_value,
            RANK() OVER (ORDER BY COUNT(*) DESC) as volume_rank
        FROM dn_master dn
        WHERE dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        {region_filter}
        GROUP BY dn.dealer_code, dn.dealer_name, dn.dealer_city, dn.dealer_region
        ORDER BY total_dns DESC
        LIMIT :limit
    """
    
    # Top Regions by Performance
    TOP_REGIONS = """
        SELECT 
            dn.dealer_region as region,
            COUNT(DISTINCT dn.dealer_code) as active_dealers,
            COUNT(*) as total_dns,
            SUM(dn.amount) as total_value,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as completed_dns,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN dn.amount ELSE 0 END) as completed_value,
            AVG(CASE WHEN dn.pod_date IS NOT NULL 
                THEN DATEDIFF(dn.pod_date, dn.shipment_date) 
                ELSE NULL END) as avg_delivery_days,
            ROUND((SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) / COUNT(*)) * 100, 2) as success_rate,
            RANK() OVER (ORDER BY SUM(dn.amount) DESC) as value_rank,
            RANK() OVER (ORDER BY COUNT(*) DESC) as volume_rank,
            RANK() OVER (ORDER BY (SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) / COUNT(*)) DESC) as efficiency_rank
        FROM dn_master dn
        WHERE dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
            AND dn.dealer_region IS NOT NULL
            AND dn.dealer_region != ''
        GROUP BY dn.dealer_region
        ORDER BY total_value DESC
        LIMIT :limit
    """
    
    # Top Warehouses by Performance
    TOP_WAREHOUSES = """
        SELECT 
            w.warehouse_code,
            w.warehouse_name,
            w.warehouse_city,
            w.warehouse_region,
            COUNT(DISTINCT dn.dn_number) as total_dns,
            SUM(dn.amount) as total_value,
            COUNT(DISTINCT d.delivery_id) as total_deliveries,
            AVG(CASE WHEN d.actual_delivery_date IS NOT NULL 
                THEN DATEDIFF(d.actual_delivery_date, d.dispatch_date) 
                ELSE NULL END) as avg_delivery_time,
            SUM(CASE WHEN dn.pgi_status = 'COMPLETED' THEN 1 ELSE 0 END) as pgi_completed,
            ROUND((w.capacity_used / w.capacity_total) * 100, 2) as capacity_utilization,
            RANK() OVER (ORDER BY SUM(dn.amount) DESC) as value_rank,
            RANK() OVER (ORDER BY COUNT(DISTINCT dn.dn_number) DESC) as volume_rank
        FROM warehouses w
        LEFT JOIN dn_master dn ON w.warehouse_code = dn.warehouse_code
            AND dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        LEFT JOIN deliveries d ON w.warehouse_code = d.warehouse_code
            AND d.dispatch_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        WHERE w.status = 'ACTIVE'
        GROUP BY w.warehouse_code, w.warehouse_name, w.warehouse_city, 
                 w.warehouse_region, w.capacity_total, w.capacity_used
        ORDER BY total_value DESC
        LIMIT :limit
    """
    
    # Top Products by Value
    TOP_PRODUCTS_BY_VALUE = """
        SELECT 
            p.product_code,
            p.product_name,
            p.category,
            p.sub_category,
            SUM(dp.quantity) as total_quantity,
            SUM(dp.net_amount) as total_value,
            COUNT(DISTINCT dp.dn_number) as order_count,
            AVG(dp.unit_price) as avg_price,
            RANK() OVER (ORDER BY SUM(dp.net_amount) DESC) as value_rank
        FROM dn_products dp
        JOIN products p ON dp.product_code = p.product_code
        WHERE dp.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        GROUP BY p.product_code, p.product_name, p.category, p.sub_category
        ORDER BY total_value DESC
        LIMIT :limit
    """
    
    # Top Products by Volume
    TOP_PRODUCTS_BY_VOLUME = """
        SELECT 
            p.product_code,
            p.product_name,
            p.category,
            SUM(dp.quantity) as total_quantity,
            SUM(dp.net_amount) as total_value,
            RANK() OVER (ORDER BY SUM(dp.quantity) DESC) as volume_rank
        FROM dn_products dp
        JOIN products p ON dp.product_code = p.product_code
        WHERE dp.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        GROUP BY p.product_code, p.product_name, p.category
        ORDER BY total_quantity DESC
        LIMIT :limit
    """
    
    # Monthly Trend
    MONTHLY_TREND = """
        SELECT 
            DATE_FORMAT(dn.dn_date, '%Y-%m') as month,
            COUNT(*) as total_dns,
            SUM(dn.amount) as total_value,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as completed_dns,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN dn.amount ELSE 0 END) as completed_value,
            AVG(DATEDIFF(dn.pod_date, dn.shipment_date)) as avg_delivery_days,
            COUNT(DISTINCT dn.dealer_code) as active_dealers
        FROM dn_master dn
        WHERE dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :months MONTH)
        GROUP BY DATE_FORMAT(dn.dn_date, '%Y-%m')
        ORDER BY month DESC
    """
    
    # Weekly Trend
    WEEKLY_TREND = """
        SELECT 
            YEAR(dn.dn_date) as year,
            WEEK(dn.dn_date) as week,
            DATE_SUB(dn.dn_date, INTERVAL WEEKDAY(dn.dn_date) DAY) as week_start,
            COUNT(*) as total_dns,
            SUM(dn.amount) as total_value,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as completed_dns,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN dn.amount ELSE 0 END) as completed_value
        FROM dn_master dn
        WHERE dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :weeks WEEK)
        GROUP BY YEAR(dn.dn_date), WEEK(dn.dn_date), week_start
        ORDER BY year DESC, week DESC
    """
    
    # Growth Analysis
    GROWTH_ANALYSIS = """
        WITH monthly_data AS (
            SELECT 
                DATE_FORMAT(dn.dn_date, '%Y-%m') as month,
                SUM(dn.amount) as total_value,
                COUNT(*) as total_dns,
                LAG(SUM(dn.amount)) OVER (ORDER BY DATE_FORMAT(dn.dn_date, '%Y-%m')) as prev_value,
                LAG(COUNT(*)) OVER (ORDER BY DATE_FORMAT(dn.dn_date, '%Y-%m')) as prev_dns
            FROM dn_master dn
            WHERE dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :months MONTH)
            GROUP BY DATE_FORMAT(dn.dn_date, '%Y-%m')
        )
        SELECT 
            month,
            total_value,
            total_dns,
            prev_value,
            prev_dns,
            CASE 
                WHEN prev_value IS NULL OR prev_value = 0 THEN 0
                ELSE ROUND(((total_value - prev_value) / prev_value) * 100, 2)
            END as value_growth,
            CASE 
                WHEN prev_dns IS NULL OR prev_dns = 0 THEN 0
                ELSE ROUND(((total_dns - prev_dns) / prev_dns) * 100, 2)
            END as volume_growth
        FROM monthly_data
        ORDER BY month DESC
    """
    
    # Region Comparison
    REGION_COMPARISON = """
        SELECT 
            dn.dealer_region as region,
            COUNT(*) as total_dns,
            SUM(dn.amount) as total_value,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as completed_dns,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN dn.amount ELSE 0 END) as completed_value,
            AVG(CASE WHEN dn.pod_date IS NOT NULL 
                THEN DATEDIFF(dn.pod_date, dn.shipment_date) 
                ELSE NULL END) as avg_delivery_days,
            ROUND((SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) / COUNT(*)) * 100, 2) as success_rate,
            COUNT(DISTINCT dn.dealer_code) as active_dealers
        FROM dn_master dn
        WHERE dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
            AND dn.dealer_region IS NOT NULL
            AND dn.dealer_region != ''
        GROUP BY dn.dealer_region
        ORDER BY total_value DESC
    """
    
    # Warehouse Comparison
    WAREHOUSE_COMPARISON = """
        SELECT 
            w.warehouse_name,
            w.warehouse_city,
            w.warehouse_region,
            COUNT(DISTINCT dn.dn_number) as total_dns,
            SUM(dn.amount) as total_value,
            COUNT(DISTINCT d.delivery_id) as total_deliveries,
            AVG(CASE WHEN d.actual_delivery_date IS NOT NULL 
                THEN DATEDIFF(d.actual_delivery_date, d.dispatch_date) 
                ELSE NULL END) as avg_delivery_time,
            SUM(CASE WHEN dn.pgi_status = 'COMPLETED' THEN 1 ELSE 0 END) as pgi_completed,
            ROUND((w.capacity_used / w.capacity_total) * 100, 2) as capacity_utilization
        FROM warehouses w
        LEFT JOIN dn_master dn ON w.warehouse_code = dn.warehouse_code
            AND dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        LEFT JOIN deliveries d ON w.warehouse_code = d.warehouse_code
            AND d.dispatch_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        WHERE w.status = 'ACTIVE'
        GROUP BY w.warehouse_name, w.warehouse_city, w.warehouse_region
        ORDER BY total_value DESC
    """
    
    # City/Region Comparison
    CITY_COMPARISON = """
        SELECT 
            dn.dealer_city as city,
            dn.dealer_region as region,
            COUNT(*) as total_dns,
            SUM(dn.amount) as total_value,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as completed_dns,
            ROUND((SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) / COUNT(*)) * 100, 2) as success_rate
        FROM dn_master dn
        WHERE dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
            AND dn.dealer_city IS NOT NULL
        GROUP BY dn.dealer_city, dn.dealer_region
        HAVING total_dns >= :min_orders
        ORDER BY total_value DESC
        LIMIT :limit
    """
    
    # Year-over-Year Comparison
    YOY_COMPARISON = """
        WITH current_year AS (
            SELECT 
                MONTH(dn.dn_date) as month_num,
                SUM(dn.amount) as current_value,
                COUNT(*) as current_dns
            FROM dn_master dn
            WHERE YEAR(dn.dn_date) = YEAR(CURRENT_DATE)
                AND dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :months MONTH)
            GROUP BY MONTH(dn.dn_date)
        ),
        previous_year AS (
            SELECT 
                MONTH(dn.dn_date) as month_num,
                SUM(dn.amount) as previous_value,
                COUNT(*) as previous_dns
            FROM dn_master dn
            WHERE YEAR(dn.dn_date) = YEAR(CURRENT_DATE) - 1
                AND dn.dn_date >= DATE_SUB(DATE_SUB(CURRENT_DATE, INTERVAL 1 YEAR), INTERVAL :months MONTH)
            GROUP BY MONTH(dn.dn_date)
        )
        SELECT 
            c.month_num,
            c.current_value,
            c.current_dns,
            p.previous_value,
            p.previous_dns,
            CASE 
                WHEN p.previous_value IS NULL OR p.previous_value = 0 THEN 0
                ELSE ROUND(((c.current_value - p.previous_value) / p.previous_value) * 100, 2)
            END as yoy_growth
        FROM current_year c
        LEFT JOIN previous_year p ON c.month_num = p.month_num
        ORDER BY c.month_num DESC
    """


# ==========================================================
# MAIN ANALYTICS SERVICE
# ==========================================================

class AnalyticsService:
    """
    Performance Analytics Engine
    Handles rankings, trends, growth analysis, and comparisons
    """
    
    def __init__(self, db: Session):
        self.db = db
        logger.info("Analytics Service initialized (v3.0)")
    
    # ==========================================================
    # RANKINGS METHODS
    # ==========================================================
    
    def get_top_dealers(self, days: int = 90, limit: int = 10, 
                       region: Optional[str] = None, 
                       city: Optional[str] = None,
                       metric: str = "value") -> List[Dict[str, Any]]:
        """
        Get top dealers by performance
        
        Args:
            days: Analysis period in days
            limit: Number of dealers to return
            region: Optional region filter
            city: Optional city filter
            metric: Ranking metric ('value' or 'volume')
        
        Returns:
            List of top dealers with performance metrics
        """
        logger.info(f"Getting top dealers by {metric} for last {days} days")
        
        try:
            region_filter = ""
            city_filter = ""
            params = {"days": days, "limit": limit}
            
            if region:
                region_filter = "AND dn.dealer_region = :region"
                params["region"] = region
            
            if city:
                city_filter = "AND dn.dealer_city = :city"
                params["city"] = city
            
            if metric == "volume":
                query = text(AnalyticsQueries.TOP_DEALERS_BY_VOLUME.format(
                    region_filter=region_filter
                ))
            else:
                query = text(AnalyticsQueries.TOP_DEALERS_BY_VALUE.format(
                    region_filter=region_filter,
                    city_filter=city_filter
                ))
            
            results = self.db.execute(query, params).fetchall()
            
            dealers = []
            for row in results:
                row_dict = dict(row._mapping)
                
                # Calculate success rate
                total_dns = row_dict.get('total_dns', 0)
                completed_dns = row_dict.get('completed_dns', 0)
                success_rate = (completed_dns / total_dns * 100) if total_dns > 0 else 0
                
                dealers.append({
                    "rank": row_dict.get(f"{metric}_rank", i + 1),
                    "dealer_code": row_dict.get('dealer_code'),
                    "dealer_name": row_dict.get('dealer_name'),
                    "dealer_city": row_dict.get('dealer_city'),
                    "dealer_region": row_dict.get('dealer_region'),
                    "total_dns": total_dns,
                    "total_value": float(row_dict.get('total_value', 0)),
                    "completed_dns": completed_dns,
                    "completed_value": float(row_dict.get('completed_value', 0)),
                    "success_rate": round(success_rate, 1),
                    "avg_delivery_days": round(row_dict.get('avg_delivery_days', 0), 1)
                })
            
            return dealers
            
        except Exception as e:
            logger.error(f"Error getting top dealers: {e}")
            return []
    
    def get_top_regions(self, days: int = 90, limit: int = 10, 
                       metric: str = "value") -> List[Dict[str, Any]]:
        """
        Get top regions by performance
        
        Args:
            days: Analysis period in days
            limit: Number of regions to return
            metric: Ranking metric ('value', 'volume', or 'efficiency')
        
        Returns:
            List of top regions with performance metrics
        """
        logger.info(f"Getting top regions for last {days} days")
        
        try:
            query = text(AnalyticsQueries.TOP_REGIONS)
            results = self.db.execute(query, {"days": days, "limit": limit}).fetchall()
            
            regions = []
            rank_field = f"{metric}_rank" if metric in ['value', 'volume', 'efficiency'] else "value_rank"
            
            for row in results:
                row_dict = dict(row._mapping)
                
                regions.append({
                    "rank": row_dict.get(rank_field, 0),
                    "region": row_dict.get('region'),
                    "active_dealers": row_dict.get('active_dealers', 0),
                    "total_dns": row_dict.get('total_dns', 0),
                    "total_value": float(row_dict.get('total_value', 0)),
                    "completed_dns": row_dict.get('completed_dns', 0),
                    "completed_value": float(row_dict.get('completed_value', 0)),
                    "success_rate": float(row_dict.get('success_rate', 0)),
                    "avg_delivery_days": round(row_dict.get('avg_delivery_days', 0), 1),
                    "efficiency_rank": row_dict.get('efficiency_rank', 0)
                })
            
            return regions
            
        except Exception as e:
            logger.error(f"Error getting top regions: {e}")
            return []
    
    def get_top_warehouses(self, days: int = 90, limit: int = 10,
                          metric: str = "value") -> List[Dict[str, Any]]:
        """
        Get top warehouses by performance
        
        Args:
            days: Analysis period in days
            limit: Number of warehouses to return
            metric: Ranking metric ('value' or 'volume')
        
        Returns:
            List of top warehouses with performance metrics
        """
        logger.info(f"Getting top warehouses for last {days} days")
        
        try:
            query = text(AnalyticsQueries.TOP_WAREHOUSES)
            results = self.db.execute(query, {"days": days, "limit": limit}).fetchall()
            
            warehouses = []
            rank_field = f"{metric}_rank" if metric in ['value', 'volume'] else "value_rank"
            
            for row in results:
                row_dict = dict(row._mapping)
                
                warehouses.append({
                    "rank": row_dict.get(rank_field, 0),
                    "warehouse_code": row_dict.get('warehouse_code'),
                    "warehouse_name": row_dict.get('warehouse_name'),
                    "warehouse_city": row_dict.get('warehouse_city'),
                    "warehouse_region": row_dict.get('warehouse_region'),
                    "total_dns": row_dict.get('total_dns', 0),
                    "total_value": float(row_dict.get('total_value', 0)),
                    "total_deliveries": row_dict.get('total_deliveries', 0),
                    "avg_delivery_time": round(row_dict.get('avg_delivery_time', 0), 1),
                    "pgi_completed": row_dict.get('pgi_completed', 0),
                    "capacity_utilization": float(row_dict.get('capacity_utilization', 0))
                })
            
            return warehouses
            
        except Exception as e:
            logger.error(f"Error getting top warehouses: {e}")
            return []
    
    def get_top_products(self, days: int = 90, limit: int = 10,
                        metric: str = "value",
                        category: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get top products by performance
        
        Args:
            days: Analysis period in days
            limit: Number of products to return
            metric: Ranking metric ('value' or 'volume')
            category: Optional category filter
        
        Returns:
            List of top products with performance metrics
        """
        logger.info(f"Getting top products by {metric} for last {days} days")
        
        try:
            if metric == "volume":
                query = text(AnalyticsQueries.TOP_PRODUCTS_BY_VOLUME)
            else:
                query = text(AnalyticsQueries.TOP_PRODUCTS_BY_VALUE)
            
            params = {"days": days, "limit": limit}
            
            if category:
                # Add category filter
                query = text(str(query) + " AND p.category = :category")
                params["category"] = category
            
            results = self.db.execute(query, params).fetchall()
            
            products = []
            rank_field = f"{metric}_rank" if metric in ['value', 'volume'] else "value_rank"
            
            for row in results:
                row_dict = dict(row._mapping)
                
                products.append({
                    "rank": row_dict.get(rank_field, 0),
                    "product_code": row_dict.get('product_code'),
                    "product_name": row_dict.get('product_name'),
                    "category": row_dict.get('category'),
                    "sub_category": row_dict.get('sub_category'),
                    "total_quantity": row_dict.get('total_quantity', 0),
                    "total_value": float(row_dict.get('total_value', 0)),
                    "order_count": row_dict.get('order_count', 0),
                    "avg_price": float(row_dict.get('avg_price', 0))
                })
            
            return products
            
        except Exception as e:
            logger.error(f"Error getting top products: {e}")
            return []
    
    # ==========================================================
    # TRENDS METHODS
    # ==========================================================
    
    def get_monthly_trend(self, months: int = 12) -> List[Dict[str, Any]]:
        """
        Get monthly trend analysis
        
        Args:
            months: Number of months to analyze
        
        Returns:
            List of monthly trend data
        """
        logger.info(f"Getting monthly trend for last {months} months")
        
        try:
            query = text(AnalyticsQueries.MONTHLY_TREND)
            results = self.db.execute(query, {"months": months}).fetchall()
            
            trends = []
            for row in results:
                row_dict = dict(row._mapping)
                
                # Calculate month-over-month growth
                if trends:
                    prev_value = trends[-1]['total_value']
                    prev_dns = trends[-1]['total_dns']
                    value_growth = ((row_dict.get('total_value', 0) - prev_value) / prev_value * 100) if prev_value > 0 else 0
                    dns_growth = ((row_dict.get('total_dns', 0) - prev_dns) / prev_dns * 100) if prev_dns > 0 else 0
                else:
                    value_growth = 0
                    dns_growth = 0
                
                trends.append({
                    "month": row_dict.get('month'),
                    "total_dns": row_dict.get('total_dns', 0),
                    "total_value": float(row_dict.get('total_value', 0)),
                    "completed_dns": row_dict.get('completed_dns', 0),
                    "completed_value": float(row_dict.get('completed_value', 0)),
                    "avg_delivery_days": round(row_dict.get('avg_delivery_days', 0), 1),
                    "active_dealers": row_dict.get('active_dealers', 0),
                    "success_rate": round((row_dict.get('completed_dns', 0) / row_dict.get('total_dns', 1)) * 100, 1),
                    "value_growth": round(value_growth, 1),
                    "dns_growth": round(dns_growth, 1)
                })
            
            return trends
            
        except Exception as e:
            logger.error(f"Error getting monthly trend: {e}")
            return []
    
    def get_weekly_trend(self, weeks: int = 12) -> List[Dict[str, Any]]:
        """
        Get weekly trend analysis
        
        Args:
            weeks: Number of weeks to analyze
        
        Returns:
            List of weekly trend data
        """
        logger.info(f"Getting weekly trend for last {weeks} weeks")
        
        try:
            query = text(AnalyticsQueries.WEEKLY_TREND)
            results = self.db.execute(query, {"weeks": weeks}).fetchall()
            
            trends = []
            for row in results:
                row_dict = dict(row._mapping)
                
                trends.append({
                    "year": row_dict.get('year'),
                    "week": row_dict.get('week'),
                    "week_start": str(row_dict.get('week_start')) if row_dict.get('week_start') else None,
                    "total_dns": row_dict.get('total_dns', 0),
                    "total_value": float(row_dict.get('total_value', 0)),
                    "completed_dns": row_dict.get('completed_dns', 0),
                    "completed_value": float(row_dict.get('completed_value', 0)),
                    "success_rate": round((row_dict.get('completed_dns', 0) / row_dict.get('total_dns', 1)) * 100, 1)
                })
            
            return trends
            
        except Exception as e:
            logger.error(f"Error getting weekly trend: {e}")
            return []
    
    def get_trend_analysis(self, period: str = "monthly", duration: int = 12) -> Dict[str, Any]:
        """
        Get comprehensive trend analysis
        
        This method is called by ai_query_service.py for analytics queries
        
        Args:
            period: 'monthly' or 'weekly'
            duration: Number of periods to analyze
        
        Returns:
            Comprehensive trend analysis with insights
        """
        logger.info(f"Getting trend analysis for {period} period")
        
        try:
            if period == "weekly":
                trends = self.get_weekly_trend(duration)
            else:
                trends = self.get_monthly_trend(duration)
            
            if not trends:
                return {
                    "trends": [],
                    "summary": {},
                    "insights": ["No data available for analysis"]
                }
            
            # Calculate overall trends
            latest = trends[0] if trends else {}
            previous = trends[1] if len(trends) > 1 else {}
            
            value_change = latest.get('total_value', 0) - previous.get('total_value', 0)
            value_growth = (value_change / previous.get('total_value', 1)) * 100 if previous.get('total_value', 0) > 0 else 0
            
            dns_change = latest.get('total_dns', 0) - previous.get('total_dns', 0)
            dns_growth = (dns_change / previous.get('total_dns', 1)) * 100 if previous.get('total_dns', 0) > 0 else 0
            
            # Generate insights
            insights = []
            
            if value_growth > 10:
                insights.append(f"📈 Strong growth of {value_growth:.1f}% in {period}ly value")
            elif value_growth < -10:
                insights.append(f"📉 Decline of {abs(value_growth):.1f}% in {period}ly value - needs attention")
            elif value_growth > 0:
                insights.append(f"📊 Positive growth of {value_growth:.1f}% in {period}ly value")
            
            if latest.get('success_rate', 0) > 95:
                insights.append(f"✅ Excellent success rate of {latest.get('success_rate', 0)}%")
            elif latest.get('success_rate', 0) < 80:
                insights.append(f"⚠️ Low success rate of {latest.get('success_rate', 0)}% - needs improvement")
            
            if latest.get('avg_delivery_days', 0) > 7:
                insights.append(f"⏰ High delivery time of {latest.get('avg_delivery_days', 0)} days - consider optimization")
            elif latest.get('avg_delivery_days', 0) < 3:
                insights.append(f"🚀 Fast delivery time of {latest.get('avg_delivery_days', 0)} days")
            
            return {
                "trends": trends,
                "summary": {
                    "latest_value": latest.get('total_value', 0),
                    "previous_value": previous.get('total_value', 0),
                    "value_growth": round(value_growth, 1),
                    "latest_dns": latest.get('total_dns', 0),
                    "previous_dns": previous.get('total_dns', 0),
                    "dns_growth": round(dns_growth, 1),
                    "latest_success_rate": latest.get('success_rate', 0),
                    "avg_delivery_days": latest.get('avg_delivery_days', 0),
                    "active_dealers": latest.get('active_dealers', 0)
                },
                "insights": insights
            }
            
        except Exception as e:
            logger.error(f"Error getting trend analysis: {e}")
            return {
                "trends": [],
                "summary": {},
                "insights": [f"Error analyzing trends: {str(e)}"]
            }
    
    # ==========================================================
    # GROWTH ANALYSIS METHODS
    # ==========================================================
    
    def get_growth_analysis(self, months: int = 6) -> Dict[str, Any]:
        """
        Get growth analysis with month-over-month metrics
        
        This method is called by ai_query_service.py for growth queries
        
        Args:
            months: Number of months to analyze
        
        Returns:
            Growth analysis with metrics and insights
        """
        logger.info(f"Getting growth analysis for last {months} months")
        
        try:
            query = text(AnalyticsQueries.GROWTH_ANALYSIS)
            results = self.db.execute(query, {"months": months}).fetchall()
            
            growth_data = []
            for row in results:
                row_dict = dict(row._mapping)
                growth_data.append({
                    "month": row_dict.get('month'),
                    "total_value": float(row_dict.get('total_value', 0)),
                    "total_dns": row_dict.get('total_dns', 0),
                    "value_growth": float(row_dict.get('value_growth', 0)),
                    "volume_growth": float(row_dict.get('volume_growth', 0))
                })
            
            if not growth_data:
                return {
                    "growth_data": [],
                    "average_growth": 0,
                    "best_month": None,
                    "worst_month": None,
                    "trend": "stable",
                    "insights": ["No growth data available"]
                }
            
            # Calculate average growth
            value_growths = [g['value_growth'] for g in growth_data if g['value_growth'] != 0]
            avg_growth = sum(value_growths) / len(value_growths) if value_growths else 0
            
            # Find best and worst months
            positive_growths = [g for g in growth_data if g['value_growth'] > 0]
            negative_growths = [g for g in growth_data if g['value_growth'] < 0]
            
            best_month = max(growth_data, key=lambda x: x['value_growth']) if growth_data else None
            worst_month = min(growth_data, key=lambda x: x['value_growth']) if growth_data else None
            
            # Determine trend
            if len(growth_data) >= 3:
                recent_trend = sum(g['value_growth'] for g in growth_data[:3]) / 3
                if recent_trend > 5:
                    trend = "accelerating"
                elif recent_trend < -5:
                    trend = "declining"
                else:
                    trend = "stable"
            else:
                trend = "insufficient data"
            
            # Generate insights
            insights = []
            
            if avg_growth > 10:
                insights.append(f"🚀 Strong average monthly growth of {avg_growth:.1f}%")
            elif avg_growth > 0:
                insights.append(f"📈 Positive average growth of {avg_growth:.1f}%")
            elif avg_growth < -5:
                insights.append(f"⚠️ Negative average growth of {avg_growth:.1f}% - needs review")
            
            if len(positive_growths) > len(negative_growths):
                insights.append(f"✅ Growing in {len(positive_growths)} out of {len(growth_data)} months")
            
            if best_month and best_month['value_growth'] > 20:
                insights.append(f"🏆 Best growth: {best_month['month']} with {best_month['value_growth']:.1f}% increase")
            
            return {
                "growth_data": growth_data,
                "average_growth": round(avg_growth, 1),
                "best_month": best_month,
                "worst_month": worst_month,
                "trend": trend,
                "insights": insights
            }
            
        except Exception as e:
            logger.error(f"Error getting growth analysis: {e}")
            return {
                "growth_data": [],
                "average_growth": 0,
                "best_month": None,
                "worst_month": None,
                "trend": "error",
                "insights": [f"Error analyzing growth: {str(e)}"]
            }
    
    # ==========================================================
    # COMPARISON METHODS
    # ==========================================================
    
    def get_region_comparison(self, days: int = 90) -> Dict[str, Any]:
        """
        Get region-wise performance comparison
        
        This method is called by ai_query_service.py for region comparison
        
        Args:
            days: Analysis period in days
        
        Returns:
            Region comparison with rankings and metrics
        """
        logger.info(f"Getting region comparison for last {days} days")
        
        try:
            query = text(AnalyticsQueries.REGION_COMPARISON)
            results = self.db.execute(query, {"days": days}).fetchall()
            
            regions = []
            for row in results:
                row_dict = dict(row._mapping)
                
                regions.append({
                    "region": row_dict.get('region'),
                    "total_dns": row_dict.get('total_dns', 0),
                    "total_value": float(row_dict.get('total_value', 0)),
                    "completed_dns": row_dict.get('completed_dns', 0),
                    "completed_value": float(row_dict.get('completed_value', 0)),
                    "success_rate": float(row_dict.get('success_rate', 0)),
                    "avg_delivery_days": round(row_dict.get('avg_delivery_days', 0), 1),
                    "active_dealers": row_dict.get('active_dealers', 0)
                })
            
            # Add rankings
            for i, region in enumerate(regions, 1):
                region['rank'] = i
            
            # Calculate total metrics
            total_value = sum(r['total_value'] for r in regions)
            total_dns = sum(r['total_dns'] for r in regions)
            avg_success_rate = sum(r['success_rate'] for r in regions) / len(regions) if regions else 0
            
            # Find top and bottom regions
            top_region = regions[0] if regions else None
            bottom_region = regions[-1] if regions else None
            
            # Generate insights
            insights = []
            
            if top_region:
                insights.append(f"🏆 Top Region: {top_region['region']} with {top_region['total_value']:,.0f} in sales")
            
            if bottom_region and bottom_region != top_region:
                insights.append(f"📊 Bottom Region: {bottom_region['region']} with {bottom_region['success_rate']:.1f}% success rate")
            
            high_performers = [r for r in regions if r['success_rate'] > 90]
            if high_performers:
                insights.append(f"✅ {len(high_performers)} regions have >90% success rate")
            
            low_performers = [r for r in regions if r['success_rate'] < 70]
            if low_performers:
                insights.append(f"⚠️ {len(low_performers)} regions need improvement (success rate <70%)")
            
            return {
                "regions": regions,
                "summary": {
                    "total_regions": len(regions),
                    "total_value": total_value,
                    "total_dns": total_dns,
                    "avg_success_rate": round(avg_success_rate, 1),
                    "top_region": top_region['region'] if top_region else None,
                    "top_region_value": top_region['total_value'] if top_region else 0
                },
                "insights": insights
            }
            
        except Exception as e:
            logger.error(f"Error getting region comparison: {e}")
            return {
                "regions": [],
                "summary": {},
                "insights": [f"Error comparing regions: {str(e)}"]
            }
    
    def get_warehouse_comparison(self, days: int = 90) -> Dict[str, Any]:
        """
        Get warehouse-wise performance comparison
        
        Args:
            days: Analysis period in days
        
        Returns:
            Warehouse comparison with metrics
        """
        logger.info(f"Getting warehouse comparison for last {days} days")
        
        try:
            query = text(AnalyticsQueries.WAREHOUSE_COMPARISON)
            results = self.db.execute(query, {"days": days}).fetchall()
            
            warehouses = []
            for row in results:
                row_dict = dict(row._mapping)
                
                warehouses.append({
                    "warehouse_name": row_dict.get('warehouse_name'),
                    "warehouse_city": row_dict.get('warehouse_city'),
                    "warehouse_region": row_dict.get('warehouse_region'),
                    "total_dns": row_dict.get('total_dns', 0),
                    "total_value": float(row_dict.get('total_value', 0)),
                    "total_deliveries": row_dict.get('total_deliveries', 0),
                    "avg_delivery_time": round(row_dict.get('avg_delivery_time', 0), 1),
                    "pgi_completed": row_dict.get('pgi_completed', 0),
                    "capacity_utilization": float(row_dict.get('capacity_utilization', 0))
                })
            
            # Add rankings
            for i, warehouse in enumerate(warehouses, 1):
                warehouse['rank'] = i
            
            # Generate insights
            insights = []
            
            if warehouses:
                best_warehouse = warehouses[0]
                insights.append(f"🏆 Best Warehouse: {best_warehouse['warehouse_name']} with {best_warehouse['total_value']:,.0f} in value")
                
                high_capacity = [w for w in warehouses if w['capacity_utilization'] > 85]
                if high_capacity:
                    insights.append(f"⚠️ {len(high_capacity)} warehouses at >85% capacity - consider expansion")
                
                fast_delivery = [w for w in warehouses if w['avg_delivery_time'] and w['avg_delivery_time'] < 3]
                if fast_delivery:
                    insights.append(f"🚀 {len(fast_delivery)} warehouses have <3 day delivery time")
            
            return {
                "warehouses": warehouses,
                "summary": {
                    "total_warehouses": len(warehouses),
                    "total_value": sum(w['total_value'] for w in warehouses),
                    "total_dns": sum(w['total_dns'] for w in warehouses),
                    "avg_delivery_time": round(sum(w['avg_delivery_time'] for w in warehouses if w['avg_delivery_time']) / len(warehouses), 1) if warehouses else 0
                },
                "insights": insights
            }
            
        except Exception as e:
            logger.error(f"Error getting warehouse comparison: {e}")
            return {
                "warehouses": [],
                "summary": {},
                "insights": [f"Error comparing warehouses: {str(e)}"]
            }
    
    def get_city_comparison(self, days: int = 90, limit: int = 20, min_orders: int = 5) -> Dict[str, Any]:
        """
        Get city-wise performance comparison
        
        Args:
            days: Analysis period in days
            limit: Maximum number of cities to return
            min_orders: Minimum orders required for inclusion
        
        Returns:
            City comparison with metrics
        """
        logger.info(f"Getting city comparison for last {days} days")
        
        try:
            query = text(AnalyticsQueries.CITY_COMPARISON)
            results = self.db.execute(
                query, 
                {"days": days, "limit": limit, "min_orders": min_orders}
            ).fetchall()
            
            cities = []
            for row in results:
                row_dict = dict(row._mapping)
                
                cities.append({
                    "city": row_dict.get('city'),
                    "region": row_dict.get('region'),
                    "total_dns": row_dict.get('total_dns', 0),
                    "total_value": float(row_dict.get('total_value', 0)),
                    "completed_dns": row_dict.get('completed_dns', 0),
                    "success_rate": float(row_dict.get('success_rate', 0))
                })
            
            return {
                "cities": cities,
                "summary": {
                    "total_cities": len(cities),
                    "total_value": sum(c['total_value'] for c in cities),
                    "avg_success_rate": round(sum(c['success_rate'] for c in cities) / len(cities), 1) if cities else 0
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting city comparison: {e}")
            return {"cities": [], "summary": {}}
    
    def get_yoy_comparison(self, months: int = 6) -> Dict[str, Any]:
        """
        Get Year-over-Year comparison
        
        Args:
            months: Number of months to compare
        
        Returns:
            YoY comparison with growth metrics
        """
        logger.info(f"Getting YoY comparison for last {months} months")
        
        try:
            query = text(AnalyticsQueries.YOY_COMPARISON)
            results = self.db.execute(query, {"months": months}).fetchall()
            
            comparison = []
            for row in results:
                row_dict = dict(row._mapping)
                
                comparison.append({
                    "month": row_dict.get('month_num'),
                    "current_value": float(row_dict.get('current_value', 0)),
                    "current_dns": row_dict.get('current_dns', 0),
                    "previous_value": float(row_dict.get('previous_value', 0)) if row_dict.get('previous_value') else 0,
                    "previous_dns": row_dict.get('previous_dns', 0) if row_dict.get('previous_dns') else 0,
                    "yoy_growth": float(row_dict.get('yoy_growth', 0))
                })
            
            # Calculate average YoY growth
            avg_growth = sum(c['yoy_growth'] for c in comparison) / len(comparison) if comparison else 0
            
            return {
                "comparison": comparison,
                "summary": {
                    "avg_yoy_growth": round(avg_growth, 1),
                    "total_current_value": sum(c['current_value'] for c in comparison),
                    "total_previous_value": sum(c['previous_value'] for c in comparison),
                    "overall_growth": round(((sum(c['current_value'] for c in comparison) - sum(c['previous_value'] for c in comparison)) / sum(c['previous_value'] for c in comparison) * 100), 1) if sum(c['previous_value'] for c in comparison) > 0 else 0
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting YoY comparison: {e}")
            return {"comparison": [], "summary": {}}
    
    # ==========================================================
    # COMPREHENSIVE ANALYTICS METHODS
    # ==========================================================
    
    def get_comprehensive_analytics(self, days: int = 90) -> Dict[str, Any]:
        """
        Get comprehensive analytics report combining all metrics
        
        Args:
            days: Analysis period in days
        
        Returns:
            Complete analytics report
        """
        logger.info(f"Getting comprehensive analytics for last {days} days")
        
        try:
            return {
                "top_dealers": self.get_top_dealers(days, 10),
                "top_regions": self.get_top_regions(days, 5),
                "top_warehouses": self.get_top_warehouses(days, 5),
                "top_products": self.get_top_products(days, 10),
                "trend_analysis": self.get_trend_analysis("monthly", 6),
                "growth_analysis": self.get_growth_analysis(6),
                "region_comparison": self.get_region_comparison(days),
                "warehouse_comparison": self.get_warehouse_comparison(days),
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting comprehensive analytics: {e}")
            return {"error": str(e)}
    
    def get_executive_dashboard(self, days: int = 90) -> Dict[str, Any]:
        """
        Get executive dashboard with key metrics and insights
        
        Args:
            days: Analysis period in days
        
        Returns:
            Executive dashboard data
        """
        logger.info(f"Getting executive dashboard for last {days} days")
        
        try:
            top_dealers = self.get_top_dealers(days, 5)
            region_comparison = self.get_region_comparison(days)
            trend = self.get_trend_analysis("monthly", 6)
            growth = self.get_growth_analysis(6)
            
            # Calculate key metrics
            total_value = sum(d['total_value'] for d in top_dealers)
            avg_success_rate = region_comparison.get('summary', {}).get('avg_success_rate', 0)
            
            return {
                "key_metrics": {
                    "total_sales_value": total_value,
                    "avg_success_rate": avg_success_rate,
                    "growth_rate": growth.get('average_growth', 0),
                    "trend_direction": trend.get('summary', {}).get('value_growth', 0)
                },
                "top_performers": {
                    "top_dealer": top_dealers[0] if top_dealers else None,
                    "top_region": region_comparison.get('regions', [])[0] if region_comparison.get('regions') else None
                },
                "insights": {
                    "growth_insights": growth.get('insights', []),
                    "trend_insights": trend.get('insights', []),
                    "region_insights": region_comparison.get('insights', [])
                },
                "recommendations": self._generate_recommendations(region_comparison, growth, trend)
            }
            
        except Exception as e:
            logger.error(f"Error getting executive dashboard: {e}")
            return {"error": str(e)}
    
    def _generate_recommendations(self, region_comparison: Dict, growth: Dict, trend: Dict) -> List[str]:
        """Generate actionable recommendations based on analytics"""
        recommendations = []
        
        # Region-based recommendations
        if region_comparison.get('regions'):
            low_performers = [r for r in region_comparison['regions'] if r.get('success_rate', 100) < 70]
            if low_performers:
                regions = ', '.join([r['region'] for r in low_performers[:3]])
                recommendations.append(f"📌 Focus on improving success rate in {regions}")
        
        # Growth-based recommendations
        if growth.get('average_growth', 0) < 0:
            recommendations.append("📌 Implement retention strategies to reverse negative growth trend")
        elif growth.get('average_growth', 0) < 5:
            recommendations.append("📌 Launch targeted campaigns to accelerate growth momentum")
        
        # Trend-based recommendations
        if trend.get('summary', {}).get('avg_delivery_days', 0) > 7:
            recommendations.append("📌 Optimize delivery operations to reduce average delivery time")
        
        if not recommendations:
            recommendations.append("✅ All metrics are performing well. Continue current strategy.")
        
        return recommendations


# ==========================================================
# COMPATIBILITY FUNCTIONS (Called by ai_query_service.py)
# ==========================================================

def get_top_dealers(db: Session, days: int = 90, limit: int = 10, 
                   region: Optional[str] = None) -> List[Dict[str, Any]]:
    """Compatibility function for top dealers"""
    service = AnalyticsService(db)
    return service.get_top_dealers(days, limit, region)


def get_top_regions(db: Session, days: int = 90, limit: int = 10) -> List[Dict[str, Any]]:
    """Compatibility function for top regions"""
    service = AnalyticsService(db)
    return service.get_top_regions(days, limit)


def get_top_products(db: Session, days: int = 90, limit: int = 10) -> List[Dict[str, Any]]:
    """Compatibility function for top products"""
    service = AnalyticsService(db)
    return service.get_top_products(days, limit)


def get_trend_analysis(db: Session, period: str = "monthly", duration: int = 12) -> Dict[str, Any]:
    """Compatibility function for trend analysis"""
    service = AnalyticsService(db)
    return service.get_trend_analysis(period, duration)


def get_growth_analysis(db: Session, months: int = 6) -> Dict[str, Any]:
    """Compatibility function for growth analysis"""
    service = AnalyticsService(db)
    return service.get_growth_analysis(months)


def get_region_comparison(db: Session, days: int = 90) -> Dict[str, Any]:
    """Compatibility function for region comparison"""
    service = AnalyticsService(db)
    return service.get_region_comparison(days)


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📊 Analytics Service v3.0 Loaded")
logger.info("   Features: Rankings | Trends | Growth | Comparisons | Insights")
logger.info("=" * 60)
