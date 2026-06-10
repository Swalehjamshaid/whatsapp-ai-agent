# ==========================================================
# FILE: app/services/kpi_service.py
# VERSION: 3.0
# PURPOSE: Executive Dashboard Engine - KPIs, Targets, Scores, Rankings, Alerts
# ARCHITECTURE: ai_query_service → kpi_service
# ==========================================================

import json
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta, date
from collections import defaultdict, Counter
from sqlalchemy import text, func, and_, or_
from sqlalchemy.orm import Session
from loguru import logger

# ==========================================================
# KPI CONSTANTS
# ==========================================================

class KPIConstants:
    """KPI definitions and targets"""
    
    # Network Health KPIs
    NETWORK_HEALTH = {
        "system_uptime": {"target": 99.9, "critical": 99.0, "weight": 0.15},
        "api_response_time": {"target": 2.0, "critical": 5.0, "weight": 0.10, "unit": "seconds"},
        "data_accuracy": {"target": 99.5, "critical": 97.0, "weight": 0.20},
        "integration_status": {"target": 100, "critical": 95, "weight": 0.15, "unit": "%"},
        "real_time_sync": {"target": 98, "critical": 90, "weight": 0.15, "unit": "%"},
        "error_rate": {"target": 1.0, "critical": 3.0, "weight": 0.25, "unit": "%", "lower_is_better": True}
    }
    
    # POD Performance KPIs
    POD_PERFORMANCE = {
        "pod_receipt_rate": {"target": 98, "critical": 90, "weight": 0.30, "unit": "%"},
        "pod_timeliness": {"target": 95, "critical": 85, "weight": 0.25, "unit": "%"},
        "pod_accuracy": {"target": 99, "critical": 95, "weight": 0.20, "unit": "%"},
        "digital_pod_ratio": {"target": 80, "critical": 60, "weight": 0.15, "unit": "%"},
        "pod_aging_days": {"target": 3, "critical": 7, "weight": 0.10, "unit": "days", "lower_is_better": True}
    }
    
    # PGI Performance KPIs
    PGI_PERFORMANCE = {
        "pgi_on_time": {"target": 96, "critical": 88, "weight": 0.35, "unit": "%"},
        "pgi_accuracy": {"target": 99.5, "critical": 98, "weight": 0.25, "unit": "%"},
        "pgi_processing_time": {"target": 24, "critical": 48, "weight": 0.20, "unit": "hours", "lower_is_better": True},
        "pgi_documentation": {"target": 98, "critical": 92, "weight": 0.20, "unit": "%"}
    }
    
    # Delivery Performance KPIs
    DELIVERY_PERFORMANCE = {
        "on_time_delivery": {"target": 95, "critical": 85, "weight": 0.35, "unit": "%"},
        "delivery_accuracy": {"target": 98, "critical": 94, "weight": 0.25, "unit": "%"},
        "first_attempt_success": {"target": 92, "critical": 85, "weight": 0.20, "unit": "%"},
        "damage_free_rate": {"target": 99, "critical": 97, "weight": 0.20, "unit": "%"}
    }
    
    # Overall Scores
    SCORE_WEIGHTS = {
        "network_health": 0.25,
        "pod_performance": 0.30,
        "pgi_performance": 0.20,
        "delivery_performance": 0.25
    }
    
    # Risk Thresholds
    RISK_LEVELS = {
        "critical": {"threshold": 70, "color": "🔴", "action": "Immediate attention required"},
        "high": {"threshold": 80, "color": "🟠", "action": "Review within 24 hours"},
        "medium": {"threshold": 90, "color": "🟡", "action": "Monitor closely"},
        "good": {"threshold": 95, "color": "🟢", "action": "On track"},
        "excellent": {"threshold": 100, "color": "💚", "action": "Exceeding targets"}
    }


# ==========================================================
# KPI QUERIES
# ==========================================================

class KPIQueries:
    """Container for KPI SQL queries"""
    
    # POD Performance Metrics
    POD_METRICS = """
        SELECT 
            COUNT(*) as total_deliveries,
            SUM(CASE WHEN pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as pod_received,
            SUM(CASE WHEN pod_status = 'RECEIVED' AND pod_date <= shipment_date + INTERVAL 3 DAY THEN 1 ELSE 0 END) as pod_timely,
            SUM(CASE WHEN pod_status = 'RECEIVED' AND pod_reference IS NOT NULL THEN 1 ELSE 0 END) as pod_digital,
            AVG(CASE WHEN pod_date IS NOT NULL 
                THEN DATEDIFF(pod_date, shipment_date) 
                ELSE NULL END) as avg_pod_aging
        FROM dn_master
        WHERE shipment_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
            AND shipment_date IS NOT NULL
    """
    
    # PGI Performance Metrics
    PGI_METRICS = """
        SELECT 
            COUNT(*) as total_orders,
            SUM(CASE WHEN pgi_status = 'COMPLETED' AND pgi_date <= dn_date + INTERVAL 1 DAY THEN 1 ELSE 0 END) as pgi_on_time,
            SUM(CASE WHEN pgi_status = 'COMPLETED' THEN 1 ELSE 0 END) as pgi_completed,
            AVG(CASE WHEN pgi_date IS NOT NULL 
                THEN TIMESTAMPDIFF(HOUR, dn_date, pgi_date) 
                ELSE NULL END) as avg_pgi_hours
        FROM dn_master
        WHERE dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
    """
    
    # Delivery Performance Metrics
    DELIVERY_METRICS = """
        SELECT 
            COUNT(*) as total_deliveries,
            SUM(CASE WHEN delivery_status = 'DELIVERED' AND actual_delivery_date <= scheduled_date THEN 1 ELSE 0 END) as on_time_deliveries,
            SUM(CASE WHEN delivery_status = 'DELIVERED' AND damage_report = 'NONE' THEN 1 ELSE 0 END) as damage_free,
            SUM(CASE WHEN delivery_status = 'DELIVERED' AND attempt_number = 1 THEN 1 ELSE 0 END) as first_attempt_success
        FROM deliveries
        WHERE dispatch_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
    """
    
    # Branch/Region Scores
    BRANCH_SCORES = """
        SELECT 
            dn.dealer_region as branch_name,
            COUNT(*) as total_dns,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as pod_received,
            SUM(CASE WHEN dn.pgi_status = 'COMPLETED' AND dn.pgi_date <= dn.dn_date + INTERVAL 1 DAY THEN 1 ELSE 0 END) as pgi_on_time,
            SUM(CASE WHEN d.delivery_status = 'DELIVERED' AND d.actual_delivery_date <= d.scheduled_date THEN 1 ELSE 0 END) as on_time_delivery,
            ROUND((SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) / COUNT(*)) * 100, 2) as pod_score,
            ROUND((SUM(CASE WHEN dn.pgi_status = 'COMPLETED' AND dn.pgi_date <= dn.dn_date + INTERVAL 1 DAY THEN 1 ELSE 0 END) / COUNT(*)) * 100, 2) as pgi_score,
            ROUND((SUM(CASE WHEN d.delivery_status = 'DELIVERED' AND d.actual_delivery_date <= d.scheduled_date THEN 1 ELSE 0 END) / COUNT(*)) * 100, 2) as delivery_score
        FROM dn_master dn
        LEFT JOIN deliveries d ON dn.dn_number = d.dn_number
        WHERE dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
        GROUP BY dn.dealer_region
        ORDER BY pod_score DESC
    """
    
    # Region Scores (Geographic)
    REGION_SCORES = """
        SELECT 
            dn.dealer_region as region_name,
            COUNT(DISTINCT dn.dealer_code) as active_dealers,
            COUNT(*) as total_dns,
            SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as pod_received,
            SUM(CASE WHEN dn.pgi_status = 'COMPLETED' AND dn.pgi_date <= dn.dn_date + INTERVAL 1 DAY THEN 1 ELSE 0 END) as pgi_on_time,
            SUM(CASE WHEN d.delivery_status = 'DELIVERED' AND d.actual_delivery_date <= d.scheduled_date THEN 1 ELSE 0 END) as on_time_delivery,
            ROUND((
                (SUM(CASE WHEN dn.pod_status = 'RECEIVED' THEN 1 ELSE 0 END) / COUNT(*) * 0.4) +
                (SUM(CASE WHEN dn.pgi_status = 'COMPLETED' AND dn.pgi_date <= dn.dn_date + INTERVAL 1 DAY THEN 1 ELSE 0 END) / COUNT(*) * 0.3) +
                (SUM(CASE WHEN d.delivery_status = 'DELIVERED' AND d.actual_delivery_date <= d.scheduled_date THEN 1 ELSE 0 END) / COUNT(*) * 0.3)
            ) * 100, 2) as overall_score
        FROM dn_master dn
        LEFT JOIN deliveries d ON dn.dn_number = d.dn_number
        WHERE dn.dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
            AND dn.dealer_region IS NOT NULL
        GROUP BY dn.dealer_region
        ORDER BY overall_score DESC
    """
    
    # Target vs Actual
    TARGET_VS_ACTUAL = """
        SELECT 
            DATE_FORMAT(dn_date, '%Y-%m') as month,
            COUNT(*) as actual_dns,
            SUM(dn.amount) as actual_value,
            SUM(CASE WHEN pod_status = 'RECEIVED' THEN 1 ELSE 0 END) as actual_pod,
            SUM(CASE WHEN pgi_status = 'COMPLETED' THEN 1 ELSE 0 END) as actual_pgi
        FROM dn_master
        WHERE dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL 12 MONTH)
        GROUP BY DATE_FORMAT(dn_date, '%Y-%m')
        ORDER BY month DESC
    """
    
    # Critical Delays
    CRITICAL_DELAYS = """
        SELECT 
            dn.dn_number,
            dn.dealer_name,
            dn.dealer_region,
            dn.amount,
            dn.shipment_date,
            DATEDIFF(CURRENT_DATE, dn.shipment_date) as delay_days,
            CASE 
                WHEN DATEDIFF(CURRENT_DATE, dn.shipment_date) > 14 THEN 'Critical'
                WHEN DATEDIFF(CURRENT_DATE, dn.shipment_date) > 7 THEN 'High'
                WHEN DATEDIFF(CURRENT_DATE, dn.shipment_date) > 3 THEN 'Medium'
                ELSE 'Low'
            END as delay_level,
            dn.pod_status,
            dn.warehouse_name
        FROM dn_master dn
        WHERE dn.pod_status IN ('PENDING', 'NOT_RECEIVED')
            AND dn.shipment_date IS NOT NULL
            AND DATEDIFF(CURRENT_DATE, dn.shipment_date) > :min_days
        ORDER BY delay_days DESC
        LIMIT :limit
    """
    
    # Risk Alerts
    RISK_ALERTS = """
        SELECT 
            'POD_DELAY' as alert_type,
            COUNT(*) as count,
            CONCAT(COUNT(*), ' deliveries pending beyond SLA') as message,
            'HIGH' as severity
        FROM dn_master
        WHERE pod_status IN ('PENDING', 'NOT_RECEIVED')
            AND shipment_date <= DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)
        
        UNION ALL
        
        SELECT 
            'PGI_BACKLOG' as alert_type,
            COUNT(*) as count,
            CONCAT(COUNT(*), ' orders pending PGI beyond 24 hours') as message,
            'MEDIUM' as severity
        FROM dn_master
        WHERE pgi_status IN ('PENDING', 'NOT_PROCESSED')
            AND dn_date <= DATE_SUB(CURRENT_DATE, INTERVAL 1 DAY)
        
        UNION ALL
        
        SELECT 
            'DELIVERY_DELAY' as alert_type,
            COUNT(*) as count,
            CONCAT(COUNT(*), ' deliveries delayed beyond schedule') as message,
            'HIGH' as severity
        FROM deliveries
        WHERE delivery_status IN ('DISPATCHED', 'IN_TRANSIT')
            AND scheduled_date <= DATE_SUB(CURRENT_DATE, INTERVAL 2 DAY)
        
        UNION ALL
        
        SELECT 
            'WAREHOUSE_CAPACITY' as alert_type,
            warehouse_name as count,
            CONCAT(warehouse_name, ' at ', ROUND((capacity_used / capacity_total) * 100, 0), '% capacity') as message,
            CASE 
                WHEN (capacity_used / capacity_total) > 0.9 THEN 'HIGH'
                WHEN (capacity_used / capacity_total) > 0.75 THEN 'MEDIUM'
                ELSE 'LOW'
            END as severity
        FROM warehouses
        WHERE (capacity_used / capacity_total) > 0.75
    """
    
    # Escalations
    ESCALATIONS = """
        SELECT 
            dn.dn_number,
            dn.dealer_name,
            dn.dealer_region,
            dn.amount,
            DATEDIFF(CURRENT_DATE, dn.shipment_date) as delay_days,
            CASE 
                WHEN DATEDIFF(CURRENT_DATE, dn.shipment_date) > 21 THEN 'VP Level'
                WHEN DATEDIFF(CURRENT_DATE, dn.shipment_date) > 14 THEN 'Director Level'
                WHEN DATEDIFF(CURRENT_DATE, dn.shipment_date) > 7 THEN 'Manager Level'
                ELSE 'Supervisor Level'
            END as escalation_level,
            dn.warehouse_name
        FROM dn_master dn
        WHERE dn.pod_status IN ('PENDING', 'NOT_RECEIVED')
            AND dn.shipment_date IS NOT NULL
            AND DATEDIFF(CURRENT_DATE, dn.shipment_date) > :min_days
        ORDER BY delay_days DESC
        LIMIT :limit
    """
    
    # Achievement Trends
    ACHIEVEMENT_TRENDS = """
        SELECT 
            DATE_FORMAT(dn_date, '%Y-%m') as month,
            COUNT(*) as actual_dns,
            SUM(dn.amount) as actual_value,
            LAG(COUNT(*)) OVER (ORDER BY DATE_FORMAT(dn_date, '%Y-%m')) as prev_dns,
            LAG(SUM(dn.amount)) OVER (ORDER BY DATE_FORMAT(dn_date, '%Y-%m')) as prev_value
        FROM dn_master
        WHERE dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL 6 MONTH)
        GROUP BY DATE_FORMAT(dn_date, '%Y-%m')
        ORDER BY month
    """


# ==========================================================
# MAIN KPI SERVICE
# ==========================================================

class KPIService:
    """
    Executive Dashboard Engine
    Handles KPIs, Targets, Scores, Rankings, and Alerts
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.constants = KPIConstants
        logger.info("KPI Service initialized (v3.0)")
    
    # ==========================================================
    # NETWORK HEALTH METHODS
    # ==========================================================
    
    def get_network_health(self, days: int = 30) -> Dict[str, Any]:
        """
        Get network health KPIs
        
        Args:
            days: Analysis period in days
        
        Returns:
            Network health metrics with scores and status
        """
        logger.info(f"Getting network health for last {days} days")
        
        try:
            # In production, these would come from actual monitoring systems
            # Here we calculate based on available data
            kpis = {}
            
            # Calculate from database metrics
            pod_metrics = self._get_pod_metrics(days)
            pgi_metrics = self._get_pgi_metrics(days)
            delivery_metrics = self._get_delivery_metrics(days)
            
            # Calculate actual metrics
            if pod_metrics.get('total_deliveries', 0) > 0:
                pod_rate = (pod_metrics.get('pod_received', 0) / pod_metrics.get('total_deliveries', 1)) * 100
            else:
                pod_rate = 0
            
            if pgi_metrics.get('total_orders', 0) > 0:
                pgi_rate = (pgi_metrics.get('pgi_completed', 0) / pgi_metrics.get('total_orders', 1)) * 100
            else:
                pgi_rate = 0
            
            if delivery_metrics.get('total_deliveries', 0) > 0:
                delivery_rate = (delivery_metrics.get('on_time_deliveries', 0) / delivery_metrics.get('total_deliveries', 1)) * 100
            else:
                delivery_rate = 0
            
            # Calculate data accuracy (based on completeness)
            data_accuracy = self._calculate_data_accuracy()
            
            # Build KPI results
            for kpi_name, kpi_config in self.constants.NETWORK_HEALTH.items():
                if kpi_name == "data_accuracy":
                    actual = data_accuracy
                elif kpi_name == "pod_performance_rate":
                    actual = pod_rate
                elif kpi_name == "pgi_performance_rate":
                    actual = pgi_rate
                elif kpi_name == "delivery_performance_rate":
                    actual = delivery_rate
                else:
                    # Default values - in production, these come from monitoring
                    actual = kpi_config['target'] - 0.5
                
                kpis[kpi_name] = self._calculate_kpi_score(
                    kpi_name, actual, kpi_config, self.constants.NETWORK_HEALTH
                )
            
            # Calculate overall score
            overall_score = sum(
                kpis[kpi]['score'] * kpis[kpi]['weight'] 
                for kpi in kpis
            )
            
            # Determine risk level
            risk_level = self._get_risk_level(overall_score)
            
            return {
                "kpis": kpis,
                "overall_score": round(overall_score, 1),
                "risk_level": risk_level,
                "status": risk_level['color'],
                "summary": self._generate_health_summary(kpis, overall_score),
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting network health: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # EXECUTIVE DASHBOARD
    # ==========================================================
    
    def get_executive_dashboard(self, days: int = 30) -> Dict[str, Any]:
        """
        Get executive dashboard with all key metrics
        
        This method is called by ai_query_service.py for dashboard queries
        
        Args:
            days: Analysis period in days
        
        Returns:
            Complete executive dashboard
        """
        logger.info(f"Getting executive dashboard for last {days} days")
        
        try:
            pod_performance = self.get_pod_performance(days)
            pgi_performance = self.get_pgi_performance(days)
            delivery_performance = self.get_delivery_performance(days)
            target_vs_actual = self.get_target_vs_actual(days)
            critical_delays = self.get_critical_delays(7, 20)
            risk_alerts = self.get_risk_alerts()
            
            # Calculate overall executive score
            executive_score = (
                pod_performance.get('overall_score', 0) * 0.35 +
                pgi_performance.get('overall_score', 0) * 0.30 +
                delivery_performance.get('overall_score', 0) * 0.35
            )
            
            return {
                "executive_summary": {
                    "overall_score": round(executive_score, 1),
                    "pod_score": pod_performance.get('overall_score', 0),
                    "pgi_score": pgi_performance.get('overall_score', 0),
                    "delivery_score": delivery_performance.get('overall_score', 0),
                    "status": self._get_risk_level(executive_score)['color'],
                    "report_date": datetime.now().strftime("%Y-%m-%d"),
                    "period": f"Last {days} days"
                },
                "pod_performance": pod_performance,
                "pgi_performance": pgi_performance,
                "delivery_performance": delivery_performance,
                "target_analysis": target_vs_actual,
                "critical_delays": critical_delays,
                "risk_alerts": risk_alerts,
                "top_priorities": self._get_top_priorities(pod_performance, pgi_performance, delivery_performance, critical_delays)
            }
            
        except Exception as e:
            logger.error(f"Error getting executive dashboard: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # BRANCH & REGION PERFORMANCE
    # ==========================================================
    
    def get_branch_performance(self, days: int = 30, limit: int = 20) -> Dict[str, Any]:
        """
        Get branch-wise performance scores
        
        Args:
            days: Analysis period in days
            limit: Maximum number of branches
        
        Returns:
            Branch performance rankings and scores
        """
        logger.info(f"Getting branch performance for last {days} days")
        
        try:
            query = text(KPIQueries.BRANCH_SCORES)
            results = self.db.execute(query, {"days": days}).fetchall()
            
            branches = []
            for row in results:
                row_dict = dict(row._mapping)
                
                # Calculate overall branch score
                pod_score = row_dict.get('pod_score', 0)
                pgi_score = row_dict.get('pgi_score', 0)
                delivery_score = row_dict.get('delivery_score', 0)
                
                overall_score = (pod_score * 0.4 + pgi_score * 0.3 + delivery_score * 0.3)
                
                branch_data = {
                    "branch_name": row_dict.get('branch_name'),
                    "total_dns": row_dict.get('total_dns', 0),
                    "pod_score": round(pod_score, 1),
                    "pgi_score": round(pgi_score, 1),
                    "delivery_score": round(delivery_score, 1),
                    "overall_score": round(overall_score, 1),
                    "risk_level": self._get_risk_level(overall_score),
                    "pod_received": row_dict.get('pod_received', 0),
                    "pgi_on_time": row_dict.get('pgi_on_time', 0),
                    "on_time_delivery": row_dict.get('on_time_delivery', 0)
                }
                branches.append(branch_data)
            
            # Add rankings
            for i, branch in enumerate(sorted(branches, key=lambda x: x['overall_score'], reverse=True), 1):
                branch['rank'] = i
            
            # Calculate benchmarks
            avg_score = sum(b['overall_score'] for b in branches) / len(branches) if branches else 0
            top_performer = branches[0] if branches else None
            bottom_performer = branches[-1] if branches else None
            
            return {
                "branches": branches[:limit],
                "summary": {
                    "total_branches": len(branches),
                    "average_score": round(avg_score, 1),
                    "top_performer": top_performer['branch_name'] if top_performer else None,
                    "top_performer_score": top_performer['overall_score'] if top_performer else 0,
                    "needs_improvement": bottom_performer['branch_name'] if bottom_performer else None,
                    "improvement_score": bottom_performer['overall_score'] if bottom_performer else 0
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting branch performance: {e}")
            return {"branches": [], "summary": {}}
    
    def get_region_performance(self, days: int = 30) -> Dict[str, Any]:
        """
        Get region-wise performance scores
        
        Args:
            days: Analysis period in days
        
        Returns:
            Region performance rankings and metrics
        """
        logger.info(f"Getting region performance for last {days} days")
        
        try:
            query = text(KPIQueries.REGION_SCORES)
            results = self.db.execute(query, {"days": days}).fetchall()
            
            regions = []
            for row in results:
                row_dict = dict(row._mapping)
                
                region_data = {
                    "region_name": row_dict.get('region_name'),
                    "active_dealers": row_dict.get('active_dealers', 0),
                    "total_dns": row_dict.get('total_dns', 0),
                    "pod_received": row_dict.get('pod_received', 0),
                    "pgi_on_time": row_dict.get('pgi_on_time', 0),
                    "on_time_delivery": row_dict.get('on_time_delivery', 0),
                    "overall_score": float(row_dict.get('overall_score', 0)),
                    "risk_level": self._get_risk_level(float(row_dict.get('overall_score', 0)))
                }
                regions.append(region_data)
            
            # Add rankings
            for i, region in enumerate(sorted(regions, key=lambda x: x['overall_score'], reverse=True), 1):
                region['rank'] = i
            
            # Calculate regional insights
            avg_score = sum(r['overall_score'] for r in regions) / len(regions) if regions else 0
            high_performers = [r for r in regions if r['overall_score'] >= 90]
            low_performers = [r for r in regions if r['overall_score'] < 70]
            
            return {
                "regions": regions,
                "summary": {
                    "total_regions": len(regions),
                    "average_score": round(avg_score, 1),
                    "high_performers": len(high_performers),
                    "low_performers": len(low_performers),
                    "best_region": regions[0]['region_name'] if regions else None,
                    "best_score": regions[0]['overall_score'] if regions else 0
                },
                "insights": self._generate_region_insights(regions)
            }
            
        except Exception as e:
            logger.error(f"Error getting region performance: {e}")
            return {"regions": [], "summary": {}, "insights": []}
    
    # ==========================================================
    # PERFORMANCE METRICS
    # ==========================================================
    
    def get_pod_performance(self, days: int = 30) -> Dict[str, Any]:
        """
        Get POD performance KPIs
        
        Args:
            days: Analysis period in days
        
        Returns:
            POD performance metrics with scores
        """
        logger.info(f"Getting POD performance for last {days} days")
        
        try:
            metrics = self._get_pod_metrics(days)
            kpis = {}
            
            total = metrics.get('total_deliveries', 0)
            if total > 0:
                pod_receipt_rate = (metrics.get('pod_received', 0) / total) * 100
                pod_timeliness = (metrics.get('pod_timely', 0) / total) * 100
                digital_pod_ratio = (metrics.get('pod_digital', 0) / total) * 100
            else:
                pod_receipt_rate = pod_timeliness = digital_pod_ratio = 0
            
            pod_accuracy = 97.5  # Default - would come from quality checks
            pod_aging_days = metrics.get('avg_pod_aging', 0)
            
            actuals = {
                "pod_receipt_rate": pod_receipt_rate,
                "pod_timeliness": pod_timeliness,
                "pod_accuracy": pod_accuracy,
                "digital_pod_ratio": digital_pod_ratio,
                "pod_aging_days": pod_aging_days
            }
            
            for kpi_name, kpi_config in self.constants.POD_PERFORMANCE.items():
                actual = actuals.get(kpi_name, 0)
                kpis[kpi_name] = self._calculate_kpi_score(
                    kpi_name, actual, kpi_config, self.constants.POD_PERFORMANCE
                )
            
            overall_score = sum(kpis[kpi]['score'] * kpis[kpi]['weight'] for kpi in kpis)
            risk_level = self._get_risk_level(overall_score)
            
            return {
                "kpis": kpis,
                "overall_score": round(overall_score, 1),
                "risk_level": risk_level,
                "metrics_summary": {
                    "total_deliveries": total,
                    "pod_received": metrics.get('pod_received', 0),
                    "pod_timely": metrics.get('pod_timely', 0),
                    "avg_aging_days": round(pod_aging_days, 1)
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting POD performance: {e}")
            return {"error": str(e)}
    
    def get_pgi_performance(self, days: int = 30) -> Dict[str, Any]:
        """
        Get PGI performance KPIs
        
        Args:
            days: Analysis period in days
        
        Returns:
            PGI performance metrics with scores
        """
        logger.info(f"Getting PGI performance for last {days} days")
        
        try:
            metrics = self._get_pgi_metrics(days)
            kpis = {}
            
            total = metrics.get('total_orders', 0)
            if total > 0:
                pgi_on_time = (metrics.get('pgi_on_time', 0) / total) * 100
                pgi_completion_rate = (metrics.get('pgi_completed', 0) / total) * 100
            else:
                pgi_on_time = pgi_completion_rate = 0
            
            pgi_accuracy = 98.5  # Default - would come from quality checks
            pgi_processing_time = metrics.get('avg_pgi_hours', 0)
            
            actuals = {
                "pgi_on_time": pgi_on_time,
                "pgi_accuracy": pgi_accuracy,
                "pgi_processing_time": pgi_processing_time,
                "pgi_completion_rate": pgi_completion_rate
            }
            
            for kpi_name, kpi_config in self.constants.PGI_PERFORMANCE.items():
                actual = actuals.get(kpi_name, 0)
                kpis[kpi_name] = self._calculate_kpi_score(
                    kpi_name, actual, kpi_config, self.constants.PGI_PERFORMANCE
                )
            
            overall_score = sum(kpis[kpi]['score'] * kpis[kpi]['weight'] for kpi in kpis)
            risk_level = self._get_risk_level(overall_score)
            
            return {
                "kpis": kpis,
                "overall_score": round(overall_score, 1),
                "risk_level": risk_level,
                "metrics_summary": {
                    "total_orders": total,
                    "pgi_completed": metrics.get('pgi_completed', 0),
                    "pgi_on_time": metrics.get('pgi_on_time', 0),
                    "avg_processing_hours": round(pgi_processing_time, 1)
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting PGI performance: {e}")
            return {"error": str(e)}
    
    def get_delivery_performance(self, days: int = 30) -> Dict[str, Any]:
        """
        Get delivery performance KPIs
        
        Args:
            days: Analysis period in days
        
        Returns:
            Delivery performance metrics with scores
        """
        logger.info(f"Getting delivery performance for last {days} days")
        
        try:
            metrics = self._get_delivery_metrics(days)
            kpis = {}
            
            total = metrics.get('total_deliveries', 0)
            if total > 0:
                on_time_delivery = (metrics.get('on_time_deliveries', 0) / total) * 100
                damage_free_rate = (metrics.get('damage_free', 0) / total) * 100
                first_attempt_success = (metrics.get('first_attempt_success', 0) / total) * 100
            else:
                on_time_delivery = damage_free_rate = first_attempt_success = 0
            
            delivery_accuracy = 97.8  # Default - would come from accuracy checks
            
            actuals = {
                "on_time_delivery": on_time_delivery,
                "delivery_accuracy": delivery_accuracy,
                "first_attempt_success": first_attempt_success,
                "damage_free_rate": damage_free_rate
            }
            
            for kpi_name, kpi_config in self.constants.DELIVERY_PERFORMANCE.items():
                actual = actuals.get(kpi_name, 0)
                kpis[kpi_name] = self._calculate_kpi_score(
                    kpi_name, actual, kpi_config, self.constants.DELIVERY_PERFORMANCE
                )
            
            overall_score = sum(kpis[kpi]['score'] * kpis[kpi]['weight'] for kpi in kpis)
            risk_level = self._get_risk_level(overall_score)
            
            return {
                "kpis": kpis,
                "overall_score": round(overall_score, 1),
                "risk_level": risk_level,
                "metrics_summary": {
                    "total_deliveries": total,
                    "on_time_deliveries": metrics.get('on_time_deliveries', 0),
                    "damage_free": metrics.get('damage_free', 0),
                    "first_attempt_success": metrics.get('first_attempt_success', 0)
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting delivery performance: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # TARGET VS ACTUAL
    # ==========================================================
    
    def get_target_vs_actual(self, days: int = 30) -> Dict[str, Any]:
        """
        Get target vs actual comparison
        
        Args:
            days: Analysis period in days
        
        Returns:
            Target achievement metrics
        """
        logger.info(f"Getting target vs actual for last {days} days")
        
        try:
            # Define targets (these would come from business rules)
            targets = {
                "total_dns": 1000,
                "total_value": 50000000,
                "pod_receipt_rate": 98,
                "pgi_completion_rate": 96,
                "on_time_delivery": 95
            }
            
            # Get actuals
            pod_metrics = self._get_pod_metrics(days)
            pgi_metrics = self._get_pgi_metrics(days)
            delivery_metrics = self._get_delivery_metrics(days)
            
            # Calculate actual values
            total_dns = pod_metrics.get('total_deliveries', 0)
            total_value = self._get_total_value(days)
            
            pod_rate = (pod_metrics.get('pod_received', 0) / total_dns * 100) if total_dns > 0 else 0
            pgi_rate = (pgi_metrics.get('pgi_completed', 0) / pgi_metrics.get('total_orders', 1) * 100) if pgi_metrics.get('total_orders', 0) > 0 else 0
            delivery_rate = (delivery_metrics.get('on_time_deliveries', 0) / delivery_metrics.get('total_deliveries', 1) * 100) if delivery_metrics.get('total_deliveries', 0) > 0 else 0
            
            actuals = {
                "total_dns": total_dns,
                "total_value": total_value,
                "pod_receipt_rate": round(pod_rate, 1),
                "pgi_completion_rate": round(pgi_rate, 1),
                "on_time_delivery": round(delivery_rate, 1)
            }
            
            # Calculate achievements
            achievements = {}
            gaps = {}
            scores = {}
            
            for metric, target in targets.items():
                actual = actuals.get(metric, 0)
                achievement = (actual / target * 100) if target > 0 else 0
                gap = actual - target
                
                achievements[metric] = round(min(achievement, 100), 1)
                gaps[metric] = round(gap, 2)
                
                if achievement >= 100:
                    scores[metric] = "✅ Exceeding"
                elif achievement >= 90:
                    scores[metric] = "🟢 On Track"
                elif achievement >= 75:
                    scores[metric] = "🟡 Needs Improvement"
                else:
                    scores[metric] = "🔴 Critical"
            
            # Get monthly trend
            trend = self._get_achievement_trend()
            
            return {
                "targets": targets,
                "actuals": actuals,
                "achievements": achievements,
                "gaps": gaps,
                "scores": scores,
                "overall_achievement": round(sum(achievements.values()) / len(achievements), 1) if achievements else 0,
                "monthly_trend": trend,
                "summary": self._generate_target_summary(achievements, gaps)
            }
            
        except Exception as e:
            logger.error(f"Error getting target vs actual: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # CONTROL TOWER - CRITICAL DELAYS, RISKS, ESCALATIONS
    # ==========================================================
    
    def get_critical_delays(self, min_days: int = 7, limit: int = 50) -> Dict[str, Any]:
        """
        Get critical delays requiring attention
        
        Args:
            min_days: Minimum delay days to consider
            limit: Maximum number of records
        
        Returns:
            Critical delays with categorization
        """
        logger.info(f"Getting critical delays beyond {min_days} days")
        
        try:
            query = text(KPIQueries.CRITICAL_DELAYS)
            results = self.db.execute(query, {"min_days": min_days, "limit": limit}).fetchall()
            
            delays = []
            for row in results:
                row_dict = dict(row._mapping)
                delays.append({
                    "dn_number": row_dict.get('dn_number'),
                    "dealer_name": row_dict.get('dealer_name'),
                    "dealer_region": row_dict.get('dealer_region'),
                    "amount": float(row_dict.get('amount', 0)),
                    "delay_days": row_dict.get('delay_days', 0),
                    "delay_level": row_dict.get('delay_level'),
                    "warehouse": row_dict.get('warehouse_name')
                })
            
            # Categorize
            critical = [d for d in delays if d['delay_level'] == 'Critical']
            high = [d for d in delays if d['delay_level'] == 'High']
            medium = [d for d in delays if d['delay_level'] == 'Medium']
            
            total_value_at_risk = sum(d['amount'] for d in delays)
            
            return {
                "total_delays": len(delays),
                "critical_count": len(critical),
                "high_count": len(high),
                "medium_count": len(medium),
                "total_value_at_risk": total_value_at_risk,
                "critical_delays": critical[:10],
                "high_delays": high[:10],
                "summary": f"{len(critical)} critical delays requiring immediate attention"
            }
            
        except Exception as e:
            logger.error(f"Error getting critical delays: {e}")
            return {"error": str(e)}
    
    def get_risk_alerts(self) -> Dict[str, Any]:
        """
        Get active risk alerts
        
        Returns:
            Risk alerts with severity levels
        """
        logger.info("Getting risk alerts")
        
        try:
            query = text(KPIQueries.RISK_ALERTS)
            results = self.db.execute(query).fetchall()
            
            alerts = []
            for row in results:
                row_dict = dict(row._mapping)
                alerts.append({
                    "type": row_dict.get('alert_type'),
                    "count": row_dict.get('count'),
                    "message": row_dict.get('message'),
                    "severity": row_dict.get('severity')
                })
            
            # Group by severity
            critical_alerts = [a for a in alerts if a['severity'] == 'HIGH']
            high_alerts = [a for a in alerts if a['severity'] == 'HIGH' and a not in critical_alerts]
            medium_alerts = [a for a in alerts if a['severity'] == 'MEDIUM']
            low_alerts = [a for a in alerts if a['severity'] == 'LOW']
            
            return {
                "total_alerts": len(alerts),
                "critical_alerts": len(critical_alerts),
                "high_alerts": len(high_alerts),
                "medium_alerts": len(medium_alerts),
                "low_alerts": len(low_alerts),
                "alerts": alerts,
                "requires_action": len(critical_alerts) + len(high_alerts) > 0
            }
            
        except Exception as e:
            logger.error(f"Error getting risk alerts: {e}")
            return {"error": str(e)}
    
    def get_escalations(self, min_days: int = 7, limit: int = 30) -> Dict[str, Any]:
        """
        Get items requiring escalation based on delay severity
        
        Args:
            min_days: Minimum delay days
            limit: Maximum number of records
        
        Returns:
            Escalation items by level
        """
        logger.info(f"Getting escalations for delays beyond {min_days} days")
        
        try:
            query = text(KPIQueries.ESCALATIONS)
            results = self.db.execute(query, {"min_days": min_days, "limit": limit}).fetchall()
            
            escalations = {
                "vp_level": [],
                "director_level": [],
                "manager_level": [],
                "supervisor_level": []
            }
            
            for row in results:
                row_dict = dict(row._mapping)
                escalation_item = {
                    "dn_number": row_dict.get('dn_number'),
                    "dealer_name": row_dict.get('dealer_name'),
                    "dealer_region": row_dict.get('dealer_region'),
                    "amount": float(row_dict.get('amount', 0)),
                    "delay_days": row_dict.get('delay_days', 0),
                    "warehouse": row_dict.get('warehouse_name')
                }
                
                level = row_dict.get('escalation_level')
                if 'VP' in level:
                    escalations["vp_level"].append(escalation_item)
                elif 'Director' in level:
                    escalations["director_level"].append(escalation_item)
                elif 'Manager' in level:
                    escalations["manager_level"].append(escalation_item)
                else:
                    escalations["supervisor_level"].append(escalation_item)
            
            return {
                "escalations": escalations,
                "summary": {
                    "vp_level_count": len(escalations["vp_level"]),
                    "director_level_count": len(escalations["director_level"]),
                    "manager_level_count": len(escalations["manager_level"]),
                    "supervisor_level_count": len(escalations["supervisor_level"]),
                    "total_escalations": sum(len(v) for v in escalations.values())
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting escalations: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    def _get_pod_metrics(self, days: int) -> Dict[str, Any]:
        """Get POD metrics from database"""
        try:
            query = text(KPIQueries.POD_METRICS)
            result = self.db.execute(query, {"days": days}).fetchone()
            return dict(result._mapping) if result else {}
        except Exception as e:
            logger.error(f"Error getting POD metrics: {e}")
            return {}
    
    def _get_pgi_metrics(self, days: int) -> Dict[str, Any]:
        """Get PGI metrics from database"""
        try:
            query = text(KPIQueries.PGI_METRICS)
            result = self.db.execute(query, {"days": days}).fetchone()
            return dict(result._mapping) if result else {}
        except Exception as e:
            logger.error(f"Error getting PGI metrics: {e}")
            return {}
    
    def _get_delivery_metrics(self, days: int) -> Dict[str, Any]:
        """Get delivery metrics from database"""
        try:
            query = text(KPIQueries.DELIVERY_METRICS)
            result = self.db.execute(query, {"days": days}).fetchone()
            return dict(result._mapping) if result else {}
        except Exception as e:
            logger.error(f"Error getting delivery metrics: {e}")
            return {}
    
    def _get_total_value(self, days: int) -> float:
        """Get total order value for period"""
        try:
            query = text("""
                SELECT COALESCE(SUM(amount), 0) as total_value
                FROM dn_master
                WHERE dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL :days DAY)
            """)
            result = self.db.execute(query, {"days": days}).fetchone()
            return float(result[0]) if result else 0
        except Exception as e:
            logger.error(f"Error getting total value: {e}")
            return 0
    
    def _calculate_data_accuracy(self) -> float:
        """Calculate data accuracy based on completeness"""
        try:
            query = text("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN dealer_name IS NOT NULL AND dealer_name != '' THEN 1 ELSE 0 END) as complete_dealer,
                    SUM(CASE WHEN amount IS NOT NULL AND amount > 0 THEN 1 ELSE 0 END) as complete_amount,
                    SUM(CASE WHEN pod_status IS NOT NULL THEN 1 ELSE 0 END) as complete_status
                FROM dn_master
                WHERE dn_date >= DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY)
            """)
            result = self.db.execute(query).fetchone()
            
            if result and result[0] > 0:
                total = result[0]
                completeness = (result[1] + result[2] + result[3]) / (total * 3) * 100
                return round(completeness, 1)
            return 98.5
            
        except Exception as e:
            logger.error(f"Error calculating data accuracy: {e}")
            return 98.5
    
    def _get_achievement_trend(self) -> List[Dict[str, Any]]:
        """Get achievement trend over time"""
        try:
            query = text(KPIQueries.ACHIEVEMENT_TRENDS)
            results = self.db.execute(query).fetchall()
            
            trends = []
            for row in results:
                row_dict = dict(row._mapping)
                
                dns_growth = 0
                value_growth = 0
                
                if row_dict.get('prev_dns') and row_dict.get('prev_dns') > 0:
                    dns_growth = ((row_dict.get('actual_dns', 0) - row_dict.get('prev_dns', 0)) / row_dict.get('prev_dns', 1)) * 100
                
                if row_dict.get('prev_value') and row_dict.get('prev_value') > 0:
                    value_growth = ((row_dict.get('actual_value', 0) - row_dict.get('prev_value', 0)) / row_dict.get('prev_value', 1)) * 100
                
                trends.append({
                    "month": row_dict.get('month'),
                    "actual_dns": row_dict.get('actual_dns', 0),
                    "actual_value": float(row_dict.get('actual_value', 0)),
                    "dns_growth": round(dns_growth, 1),
                    "value_growth": round(value_growth, 1)
                })
            
            return trends
            
        except Exception as e:
            logger.error(f"Error getting achievement trend: {e}")
            return []
    
    def _calculate_kpi_score(self, name: str, actual: float, config: Dict, kpi_set: Dict) -> Dict[str, Any]:
        """Calculate individual KPI score"""
        target = config.get('target', 100)
        critical = config.get('critical', 70)
        lower_is_better = config.get('lower_is_better', False)
        
        if lower_is_better:
            # For metrics where lower is better (e.g., response time, aging)
            if actual <= target:
                score = 100
            elif actual <= critical:
                # Linear interpolation between target and critical
                score = 100 - ((actual - target) / (critical - target)) * 30
            else:
                score = max(0, 70 - ((actual - critical) / critical) * 70)
        else:
            # For metrics where higher is better
            if actual >= target:
                score = 100
            elif actual >= critical:
                score = 70 + ((actual - critical) / (target - critical)) * 30
            else:
                score = (actual / critical) * 70
        
        achievement = (actual / target * 100) if target > 0 and not lower_is_better else \
                      (target / actual * 100) if actual > 0 and lower_is_better else 0
        
        return {
            "name": name.replace('_', ' ').title(),
            "actual": round(actual, 1),
            "target": target,
            "critical": critical,
            "score": round(min(score, 100), 1),
            "achievement": round(min(achievement, 100), 1),
            "gap": round(actual - target, 1),
            "weight": config.get('weight', 0),
            "unit": config.get('unit', '%'),
            "status": self._get_status_color(score)
        }
    
    def _get_risk_level(self, score: float) -> Dict[str, Any]:
        """Get risk level based on score"""
        for level, config in self.constants.RISK_LEVELS.items():
            if score >= config['threshold']:
                return {
                    "level": level,
                    "color": config['color'],
                    "action": config['action'],
                    "threshold": config['threshold']
                }
        return self.constants.RISK_LEVELS['critical']
    
    def _get_status_color(self, score: float) -> str:
        """Get status emoji based on score"""
        if score >= 95:
            return "💚"
        elif score >= 90:
            return "🟢"
        elif score >= 80:
            return "🟡"
        elif score >= 70:
            return "🟠"
        else:
            return "🔴"
    
    def _generate_health_summary(self, kpis: Dict, overall_score: float) -> str:
        """Generate network health summary"""
        if overall_score >= 95:
            return "Network health is excellent. All systems operating optimally."
        elif overall_score >= 90:
            return "Network health is good. Minor improvements possible."
        elif overall_score >= 80:
            return "Network health is acceptable. Some areas need attention."
        elif overall_score >= 70:
            return "Network health is concerning. Immediate review recommended."
        else:
            return "Network health is critical. Urgent action required."
    
    def _generate_target_summary(self, achievements: Dict, gaps: Dict) -> str:
        """Generate target achievement summary"""
        avg_achievement = sum(achievements.values()) / len(achievements) if achievements else 0
        
        if avg_achievement >= 100:
            return "🎉 All targets exceeded! Outstanding performance!"
        elif avg_achievement >= 95:
            return "✅ Most targets achieved. Excellent progress!"
        elif avg_achievement >= 90:
            return "📊 Targets mostly on track. Keep up the momentum!"
        elif avg_achievement >= 80:
            return "⚠️ Some targets below expectations. Focus on improvement areas."
        else:
            return "🔴 Multiple targets missed. Immediate action required!"
    
    def _generate_region_insights(self, regions: List[Dict]) -> List[str]:
        """Generate region performance insights"""
        insights = []
        
        if not regions:
            return insights
        
        best = regions[0]
        worst = regions[-1]
        
        insights.append(f"🏆 Best performing region: {best['region_name']} with score {best['overall_score']}")
        
        if worst['overall_score'] < 70:
            insights.append(f"⚠️ Region needing attention: {worst['region_name']} (Score: {worst['overall_score']})")
        
        high_performers = [r for r in regions if r['overall_score'] >= 90]
        if len(high_performers) >= 3:
            insights.append(f"📈 {len(high_performers)} regions achieving excellent scores")
        
        return insights
    
    def _get_top_priorities(self, pod: Dict, pgi: Dict, delivery: Dict, delays: Dict) -> List[str]:
        """Get top priority actions based on current performance"""
        priorities = []
        
        if pod.get('overall_score', 0) < 80:
            priorities.append("Improve POD receipt rate and timeliness")
        
        if pgi.get('overall_score', 0) < 80:
            priorities.append("Reduce PGI processing backlog")
        
        if delivery.get('overall_score', 0) < 80:
            priorities.append("Enhance on-time delivery performance")
        
        if delays.get('critical_count', 0) > 0:
            priorities.append(f"Address {delays.get('critical_count', 0)} critical delivery delays")
        
        if not priorities:
            priorities.append("Maintain current performance levels")
        
        return priorities


# ==========================================================
# COMPATIBILITY FUNCTIONS (Called by ai_query_service.py)
# ==========================================================

def get_network_health(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility function for network health"""
    service = KPIService(db)
    return service.get_network_health(days)


def get_executive_dashboard(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility function for executive dashboard"""
    service = KPIService(db)
    return service.get_executive_dashboard(days)


def get_branch_performance(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility function for branch performance"""
    service = KPIService(db)
    return service.get_branch_performance(days)


def get_region_performance(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility function for region performance"""
    service = KPIService(db)
    return service.get_region_performance(days)


def get_target_vs_actual(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility function for target vs actual"""
    service = KPIService(db)
    return service.get_target_vs_actual(days)


def get_pod_performance(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility function for POD performance"""
    service = KPIService(db)
    return service.get_pod_performance(days)


def get_pgi_performance(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility function for PGI performance"""
    service = KPIService(db)
    return service.get_pgi_performance(days)


def get_all_kpis(db: Session, time_period: Dict = None) -> Dict[str, Any]:
    """Compatibility function for getting all KPIs"""
    service = KPIService(db)
    days = 30
    if time_period and time_period.get('type') == 'month':
        days = 30
    elif time_period and time_period.get('type') == 'week':
        days = 7
    elif time_period and time_period.get('type') == 'today':
        days = 1
    
    return {
        "network_health": service.get_network_health(days),
        "pod_performance": service.get_pod_performance(days),
        "pgi_performance": service.get_pgi_performance(days),
        "delivery_performance": service.get_delivery_performance(days),
        "metrics": {
            "overall_score": 0,
            "highlights": []
        }
    }


def get_dashboard_summary(db: Session) -> Dict[str, Any]:
    """Compatibility function for dashboard summary"""
    service = KPIService(db)
    dashboard = service.get_executive_dashboard(30)
    
    summary = dashboard.get('executive_summary', {})
    
    return {
        "total_dns": 0,
        "pending_pods": 0,
        "completion_rate": summary.get('pod_score', 0),
        "total_value": 0,
        "active_dealers": 0,
        "top_dealer": "N/A",
        "avg_aging": 0
    }


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📊 KPI Service v3.0 Loaded - Executive Dashboard Engine")
logger.info("   Features: Network Health | POD | PGI | Delivery | Targets | Risk Alerts")
logger.info("=" * 60)
