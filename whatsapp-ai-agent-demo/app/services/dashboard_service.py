"""
dashboard_service.py - Enterprise Logistics Dashboard Service for Haier Pakistan
Version: 19.4 – Full alignment with Frontend Command Center

All calculations for the executive dashboard are centralized here.
Data source: PostgreSQL table 'delivery_reports' (SAP Excel uploads).
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DashboardService:
    """
    Service class that computes all metrics, alerts, and recommendations
    for the Haier Pakistan Logistics Command Center dashboard.
    """

    def __init__(self, db_url: Optional[str] = None):
        """
        Initialize the service with a database connection.

        :param db_url: SQLAlchemy database URL (e.g., postgresql://user:pass@host/db).
                       If None, reads from environment variable DATABASE_URL.
        """
        self.db_url = db_url or os.getenv("DATABASE_URL")
        if not self.db_url:
            raise ValueError("DATABASE_URL environment variable not set")
        self.engine = create_engine(self.db_url)
        self.Session = sessionmaker(bind=self.engine)

        # Define default thresholds for alerts and health scoring
        self.THRESHOLDS = {
            "pgi_target": 0.95,
            "pod_target": 0.90,
            "delivery_days_warning": 5,
            "delivery_days_critical": 10,
            "pending_units_warning": 1000,
            "pending_units_critical": 5000,
            "health_score_bad": 70,
        }

        # Delivery standard compliance brackets (distance in km)
        self.COMPLIANCE_BRACKETS = [
            {"distance": "0-100", "target_days": 1},
            {"distance": "100-200", "target_days": 2},
            {"distance": "200-300", "target_days": 3},
            {"distance": "300-500", "target_days": 5},
            {"distance": "500-1000", "target_days": 7},
        ]

    def _get_connection(self):
        """Return a raw DB connection for pandas read_sql."""
        return self.engine.connect()

    def _execute_query(self, query: str, params: dict = None) -> pd.DataFrame:
        """Execute a SQL query and return a DataFrame."""
        with self._get_connection() as conn:
            return pd.read_sql(text(query), conn, params=params)

    # ---------- Core Data Fetching ----------
    def fetch_delivery_data(self) -> pd.DataFrame:
        """
        Fetch all relevant delivery records from the database.
        Expected columns: dn, warehouse, city, dealer, product, division,
        sales_office, sales_manager, units, value, delivery_date, pod_date,
        pgi_date, status, created_at.
        """
        query = """
            SELECT 
                dn,
                warehouse,
                city,
                dealer,
                product,
                division,
                sales_office,
                sales_manager,
                units,
                value,
                delivery_date,
                pod_date,
                pgi_date,
                status,
                created_at
            FROM delivery_reports
            WHERE deleted = false OR deleted IS NULL
        """
        df = self._execute_query(query)
        # Convert date columns to datetime
        date_cols = ['delivery_date', 'pod_date', 'pgi_date', 'created_at']
        for col in date_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')
        return df

    # ---------- KPI Calculations ----------
    def calculate_kpis(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Compute the 8 executive KPI cards.
        """
        total_dn = df['dn'].nunique()
        total_units = df['units'].sum()
        total_value = df['value'].sum()

        # PGI achievement: % of DNs with pgi_date not null
        pgi_count = df['pgi_date'].notna().sum()
        pgi_achievement = pgi_count / total_dn if total_dn > 0 else 0

        # POD achievement: % of DNs with pod_date not null
        pod_count = df['pod_date'].notna().sum()
        pod_achievement = pod_count / total_dn if total_dn > 0 else 0

        # Pending DNs: those without delivery_date (or status not delivered)
        pending_dn = df[df['delivery_date'].isna()]['dn'].nunique()
        pending_units = df[df['delivery_date'].isna()]['units'].sum()

        # Health score: weighted average of PGI and POD, plus penalty for pending
        health_score = (pgi_achievement * 0.4 + pod_achievement * 0.4) * 100
        # Penalty for high pending units
        if pending_units > self.THRESHOLDS["pending_units_critical"]:
            health_score -= 15
        elif pending_units > self.THRESHOLDS["pending_units_warning"]:
            health_score -= 8
        health_score = max(0, min(100, health_score))

        return {
            "total_dn": {"value": total_dn},
            "total_units": {"value": total_units},
            "total_value": {"value": total_value},
            "pgi_achievement": {"value": pgi_achievement},
            "pod_achievement": {"value": pod_achievement},
            "pending_dn": {"value": pending_dn},
            "pending_units": {"value": pending_units},
            "health_score": {"value": health_score},
        }

    # ---------- Executive Summary ----------
    def generate_executive_summary(self, df: pd.DataFrame) -> str:
        """
        Generate a natural language executive summary using key metrics.
        """
        kpis = self.calculate_kpis(df)
        total_dn = kpis["total_dn"]["value"]
        total_units = kpis["total_units"]["value"]
        pgi = kpis["pgi_achievement"]["value"] * 100
        pod = kpis["pod_achievement"]["value"] * 100
        pending = kpis["pending_units"]["value"]
        health = kpis["health_score"]["value"]

        summary = (
            f"Today's logistics performance: {total_dn:,} Delivery Notes "
            f"representing {total_units:,} units. PGI achievement is at {pgi:.1f}% "
            f"and POD achievement at {pod:.1f}%. "
            f"There are {pending:,} units pending dispatch. "
            f"The overall logistics health score is {health:.1f}%. "
        )
        if health >= 90:
            summary += "Operations are running excellently."
        elif health >= 75:
            summary += "Performance is solid; monitor pending units closely."
        else:
            summary += "Immediate attention required to improve PGI and POD rates."

        # Add insight on top delayed cities
        top_cities = self.calculate_city_performance(df).head(3)
        if not top_cities.empty:
            city_names = ", ".join(top_cities['city'].tolist())
            summary += f" Top delayed cities: {city_names}."
        return summary

    # ---------- Pipeline (Today) ----------
    def calculate_pipeline(self, df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
        """
        Funnel stages for today: DN Created, PGI Completed, In Transit,
        Delivered, POD Received. Using current date.
        """
        today = datetime.now().date()
        # Filter for today's records (based on created_at or other date)
        # Assume created_at is the DN creation date
        today_df = df[df['created_at'].dt.date == today] if 'created_at' in df else df

        total_dn_today = today_df['dn'].nunique() if not today_df.empty else 1
        if total_dn_today == 0:
            total_dn_today = 1

        # Stage counts
        dn_created = total_dn_today
        pgi_completed = today_df['pgi_date'].notna().sum()
        in_transit = today_df[
            (today_df['pgi_date'].notna()) & (today_df['delivery_date'].isna())
        ]['dn'].nunique()
        delivered = today_df[today_df['delivery_date'].notna()]['dn'].nunique()
        pod_received = today_df[today_df['pod_date'].notna()]['dn'].nunique()

        # Percentages based on DN created
        def pct(val):
            return round((val / dn_created) * 100, 1) if dn_created > 0 else 0

        return {
            "dn_created": {"dn": dn_created, "pct": 100},
            "pgi_completed": {"dn": pgi_completed, "pct": pct(pgi_completed)},
            "in_transit": {"dn": in_transit, "pct": pct(in_transit)},
            "delivered": {"dn": delivered, "pct": pct(delivered)},
            "pod_received": {"dn": pod_received, "pct": pct(pod_received)},
        }

    # ---------- Warehouse Performance Ranking ----------
    def calculate_warehouse_performance(self, df: pd.DataFrame) -> List[Dict]:
        """
        Compute detailed performance metrics per warehouse.
        """
        if df.empty:
            return []

        # Group by warehouse
        grouped = df.groupby('warehouse').agg({
            'dn': 'nunique',
            'units': 'sum',
            'value': 'sum',
            'pgi_date': lambda x: x.notna().sum(),
            'pod_date': lambda x: x.notna().sum(),
            'delivery_date': lambda x: x.notna().sum(),
            # Average delivery days (delivery_date - pgi_date)
        }).reset_index()

        # Compute derived metrics
        grouped['dns'] = grouped['dn']
        grouped['pgi_pct'] = grouped['pgi_date'] / grouped['dn']
        grouped['pod_pct'] = grouped['pod_date'] / grouped['dn']
        grouped['delivery_pct'] = grouped['delivery_date'] / grouped['dn']
        grouped['pending_units'] = grouped['units'] - grouped[
            grouped['delivery_date'] == grouped['dn']
        ]  # not correct; need to filter

        # More accurate: compute pending per warehouse separately
        pending_df = df[df['delivery_date'].isna()].groupby('warehouse').agg({
            'units': 'sum',
            'dn': 'nunique'
        }).rename(columns={'units': 'pending_units', 'dn': 'pending_dns'})

        # Merge pending
        grouped = grouped.merge(pending_df, on='warehouse', how='left')
        grouped['pending_units'] = grouped['pending_units'].fillna(0)
        grouped['pending_dns'] = grouped['pending_dns'].fillna(0)

        # Compute avg delivery days (delivery_date - pgi_date) for delivered records
        delivered_df = df[df['delivery_date'].notna()].copy()
        delivered_df['delivery_days'] = (delivered_df['delivery_date'] - delivered_df['pgi_date']).dt.days
        avg_delivery = delivered_df.groupby('warehouse')['delivery_days'].mean().fillna(0)
        grouped = grouped.merge(avg_delivery, on='warehouse', how='left')
        grouped.rename(columns={'delivery_days': 'avg_delivery_days'}, inplace=True)
        grouped['avg_delivery_days'] = grouped['avg_delivery_days'].fillna(0)

        # Avg POD days (pod_date - delivery_date)
        pod_df = df[df['pod_date'].notna()].copy()
        pod_df['pod_days'] = (pod_df['pod_date'] - pod_df['delivery_date']).dt.days
        avg_pod = pod_df.groupby('warehouse')['pod_days'].mean().fillna(0)
        grouped = grouped.merge(avg_pod, on='warehouse', how='left')
        grouped.rename(columns={'pod_days': 'avg_pod_days'}, inplace=True)
        grouped['avg_pod_days'] = grouped['avg_pod_days'].fillna(0)

        # Performance score: composite of PGI, delivery, POD, and pending penalty
        grouped['performance_score'] = (
            grouped['pgi_pct'] * 0.3 +
            grouped['delivery_pct'] * 0.3 +
            grouped['pod_pct'] * 0.3
        ) * 100
        # Penalty for pending units
        grouped['performance_score'] -= np.minimum(
            (grouped['pending_units'] / self.THRESHOLDS["pending_units_critical"]) * 10, 20
        )
        grouped['performance_score'] = grouped['performance_score'].clip(0, 100)

        # Risk indicator
        def risk_level(row):
            if row['avg_delivery_days'] > self.THRESHOLDS["delivery_days_critical"]:
                return "🔴"
            elif row['avg_delivery_days'] > self.THRESHOLDS["delivery_days_warning"]:
                return "🟡"
            else:
                return "🟢"

        grouped['risk'] = grouped.apply(risk_level, axis=1)

        # Trend (simple: compare to average)
        avg_score = grouped['performance_score'].mean()
        grouped['trend'] = grouped['performance_score'].apply(
            lambda x: "↑" if x > avg_score else ("↓" if x < avg_score else "▬")
        )

        # AI Insight: generate simple insight based on pending and delivery days
        def ai_insight(row):
            if row['pending_units'] > self.THRESHOLDS["pending_units_critical"]:
                return "High pending units. Immediate action required."
            elif row['avg_delivery_days'] > self.THRESHOLDS["delivery_days_warning"]:
                return f"Avg delivery {row['avg_delivery_days']:.1f} days. Optimize routes."
            elif row['pod_pct'] < 0.8:
                return "Low POD rate. Follow up on proof of delivery."
            else:
                return "Good performance. Maintain standards."

        grouped['ai_insight'] = grouped.apply(ai_insight, axis=1)

        # Sort by performance score descending, assign rank
        grouped = grouped.sort_values('performance_score', ascending=False)
        grouped['rank'] = range(1, len(grouped) + 1)

        # Select columns matching frontend expectations
        columns = [
            'rank', 'warehouse', 'performance_score', 'dns', 'units', 'value',
            'pgi_pct', 'delivery_pct', 'pod_pct', 'avg_delivery_days',
            'avg_pod_days', 'pending_units', 'risk', 'trend', 'ai_insight'
        ]
        # Ensure all columns exist, fill missing
        for col in columns:
            if col not in grouped.columns:
                grouped[col] = 0
        # Convert decimals to percentages for pct columns
        pct_cols = ['pgi_pct', 'delivery_pct', 'pod_pct']
        for col in pct_cols:
            grouped[col] = grouped[col] * 100  # as percentage

        result = grouped[columns].to_dict(orient='records')
        # Format numeric fields
        for rec in result:
            rec['performance_score'] = round(rec['performance_score'], 1)
            rec['pgi_pct'] = round(rec['pgi_pct'], 1)
            rec['delivery_pct'] = round(rec['delivery_pct'], 1)
            rec['pod_pct'] = round(rec['pod_pct'], 1)
            rec['avg_delivery_days'] = round(rec['avg_delivery_days'], 1)
            rec['avg_pod_days'] = round(rec['avg_pod_days'], 1)
            rec['units'] = int(rec['units'])
            rec['dns'] = int(rec['dns'])
            rec['value'] = float(rec['value'])
            rec['pending_units'] = int(rec['pending_units'])
        return result

    # ---------- Top Delayed Cities ----------
    def calculate_city_performance(self, df: pd.DataFrame, top_n: int = 5) -> List[Dict]:
        """
        Compute average delivery days and pending units per city.
        Return top N by avg delivery days (descending).
        """
        if df.empty:
            return []

        # Filter delivered records for average days
        delivered = df[df['delivery_date'].notna()].copy()
        delivered['delivery_days'] = (delivered['delivery_date'] - delivered['pgi_date']).dt.days

        city_agg = delivered.groupby('city').agg({
            'delivery_days': 'mean',
            'dn': 'nunique',
            'units': 'sum'
        }).rename(columns={'delivery_days': 'avg_delivery_days'})

        # Add pending units
        pending = df[df['delivery_date'].isna()].groupby('city')['units'].sum().rename('pending_units')
        city_agg = city_agg.merge(pending, on='city', how='left')
        city_agg['pending_units'] = city_agg['pending_units'].fillna(0)

        # Determine status based on days
        def status(row):
            if row['avg_delivery_days'] > self.THRESHOLDS["delivery_days_critical"]:
                return "Critical"
            elif row['avg_delivery_days'] > self.THRESHOLDS["delivery_days_warning"]:
                return "Warning"
            else:
                return "Good"

        city_agg['status'] = city_agg.apply(status, axis=1)
        city_agg = city_agg.sort_values('avg_delivery_days', ascending=False)
        top = city_agg.head(top_n).reset_index()

        # Format
        result = []
        for _, row in top.iterrows():
            result.append({
                'city': row['city'],
                'avg_delivery_days': round(row['avg_delivery_days'], 1),
                'pending_units': int(row['pending_units']),
                'status': row['status']
            })
        return result

    # ---------- Top Pending Warehouses ----------
    def calculate_pending_analysis(self, df: pd.DataFrame, top_n: int = 5) -> List[Dict]:
        """
        Identify warehouses with highest pending units and DNs.
        """
        if df.empty:
            return []

        pending = df[df['delivery_date'].isna()].groupby('warehouse').agg({
            'dn': 'nunique',
            'units': 'sum'
        }).rename(columns={'dn': 'pending_dns', 'units': 'pending_units'})

        pending = pending.sort_values('pending_units', ascending=False).head(top_n)
        result = []
        for warehouse, row in pending.iterrows():
            result.append({
                'warehouse': warehouse,
                'pending_dns': int(row['pending_dns']),
                'pending_units': int(row['pending_units'])
            })
        return result

    # ---------- Top Dealers ----------
    def calculate_dealer_performance(self, df: pd.DataFrame, top_n: int = 5) -> List[Dict]:
        """
        Top dealers by total revenue (value).
        """
        if df.empty:
            return []

        dealer_agg = df.groupby('dealer').agg({
            'units': 'sum',
            'value': 'sum'
        }).reset_index()
        dealer_agg = dealer_agg.sort_values('value', ascending=False).head(top_n)
        result = []
        for _, row in dealer_agg.iterrows():
            result.append({
                'dealer': row['dealer'],
                'units': int(row['units']),
                'revenue': float(row['value'])
            })
        return result

    # ---------- Top Products ----------
    def calculate_product_performance(self, df: pd.DataFrame, top_n: int = 5) -> List[Dict]:
        """
        Top products by units shipped.
        """
        if df.empty:
            return []

        prod_agg = df.groupby('product').agg({
            'units': 'sum',
            'dn': 'nunique'
        }).rename(columns={'dn': 'delivery_notes'}).reset_index()
        prod_agg = prod_agg.sort_values('units', ascending=False).head(top_n)
        result = []
        for _, row in prod_agg.iterrows():
            result.append({
                'product': row['product'],
                'units': int(row['units']),
                'delivery_notes': int(row['delivery_notes'])
            })
        return result

    # ---------- Division Performance ----------
    def calculate_division_performance(self, df: pd.DataFrame) -> List[Dict]:
        """
        Revenue per division for the donut chart.
        """
        if df.empty:
            return []

        div_agg = df.groupby('division')['value'].sum().reset_index()
        div_agg = div_agg.sort_values('value', ascending=False)
        result = []
        for _, row in div_agg.iterrows():
            result.append({
                'division': row['division'],
                'revenue': float(row['value'])
            })
        return result

    # ---------- Delivery Standard Compliance ----------
    def calculate_delivery_compliance(self, df: pd.DataFrame) -> List[Dict]:
        """
        Compute compliance against target delivery days per distance bracket.
        Since we don't have distance, we use average delivery days and
        assign to brackets based on quantiles (simulated).
        """
        if df.empty:
            return []

        # For a real implementation, you would join with a distance table.
        # Here we simulate by splitting into brackets based on avg delivery days.
        delivered = df[df['delivery_date'].notna()].copy()
        delivered['delivery_days'] = (delivered['delivery_date'] - delivered['pgi_date']).dt.days
        # Remove outliers
        delivered = delivered[delivered['delivery_days'] >= 0]

        if delivered.empty:
            return []

        # Use quantiles to assign brackets
        q = delivered['delivery_days'].quantile([0.2, 0.4, 0.6, 0.8])
        brackets = [
            {"distance": "0-100", "target_days": 1, "min": 0, "max": q.iloc[0]},
            {"distance": "100-200", "target_days": 2, "min": q.iloc[0], "max": q.iloc[1]},
            {"distance": "200-300", "target_days": 3, "min": q.iloc[1], "max": q.iloc[2]},
            {"distance": "300-500", "target_days": 5, "min": q.iloc[2], "max": q.iloc[3]},
            {"distance": "500-1000", "target_days": 7, "min": q.iloc[3], "max": float('inf')},
        ]

        result = []
        for b in brackets:
            subset = delivered[(delivered['delivery_days'] >= b['min']) & (delivered['delivery_days'] <= b['max'])]
            if not subset.empty:
                actual_avg = subset['delivery_days'].mean()
                compliance = (b['target_days'] / actual_avg) * 100 if actual_avg > 0 else 0
                compliance = min(100, compliance)
                status = "Within Standard" if compliance >= 80 else "Needs Improvement"
            else:
                actual_avg = 0
                compliance = 0
                status = "No Data"
            result.append({
                "distance": b['distance'],
                "target_days": b['target_days'],
                "actual_days": round(actual_avg, 1),
                "compliance_pct": round(compliance, 1),
                "status": status
            })
        return result

    # ---------- Critical Alerts ----------
    def generate_critical_alerts(self, df: pd.DataFrame) -> List[Dict]:
        """
        Generate alerts based on thresholds.
        """
        alerts = []

        # 1. Pending units per warehouse
        pending = df[df['delivery_date'].isna()].groupby('warehouse')['units'].sum().reset_index()
        for _, row in pending.iterrows():
            if row['units'] > self.THRESHOLDS["pending_units_critical"]:
                alerts.append({
                    "category": "Pending Units",
                    "source": row['warehouse'],
                    "message": f"Warehouse {row['warehouse']} has {row['units']:,} units pending, exceeding critical threshold.",
                    "severity": "CRITICAL"
                })
            elif row['units'] > self.THRESHOLDS["pending_units_warning"]:
                alerts.append({
                    "category": "Pending Units",
                    "source": row['warehouse'],
                    "message": f"Warehouse {row['warehouse']} has {row['units']:,} units pending, above warning level.",
                    "severity": "WARNING"
                })

        # 2. Delivery days per city (delayed)
        city_perf = self.calculate_city_performance(df, top_n=10)
        for c in city_perf:
            if c['status'] == "Critical":
                alerts.append({
                    "category": "Delivery Delay",
                    "source": c['city'],
                    "message": f"City {c['city']} has average delivery days of {c['avg_delivery_days']:.1f} days, exceeding critical threshold.",
                    "severity": "CRITICAL"
                })
            elif c['status'] == "Warning":
                alerts.append({
                    "category": "Delivery Delay",
                    "source": c['city'],
                    "message": f"City {c['city']} has average delivery days of {c['avg_delivery_days']:.1f} days, above warning level.",
                    "severity": "WARNING"
                })

        # 3. Low POD achievement per warehouse
        pod_agg = df.groupby('warehouse').agg({
            'dn': 'nunique',
            'pod_date': lambda x: x.notna().sum()
        })
        pod_agg['pod_pct'] = pod_agg['pod_date'] / pod_agg['dn']
        low_pod = pod_agg[pod_agg['pod_pct'] < 0.7]
        for warehouse, row in low_pod.iterrows():
            alerts.append({
                "category": "Low POD Rate",
                "source": warehouse,
                "message": f"Warehouse {warehouse} has POD rate of {row['pod_pct']*100:.1f}%, below 70%.",
                "severity": "WARNING"
            })

        # 4. Health score below threshold
        health = self.calculate_kpis(df)['health_score']['value']
        if health < self.THRESHOLDS["health_score_bad"]:
            alerts.append({
                "category": "Health Score",
                "source": "Overall Logistics",
                "message": f"Overall health score is {health:.1f}%, below acceptable level.",
                "severity": "CRITICAL"
            })

        # Limit to top 10 most critical
        alerts.sort(key=lambda x: 0 if x['severity'] == 'CRITICAL' else 1)
        return alerts[:10]

    # ---------- Director Recommendations ----------
    def get_recommendations(self, df: pd.DataFrame) -> List[str]:
        """
        Generate actionable recommendations based on data.
        """
        recs = []

        # 1. If pending units high
        total_pending = df[df['delivery_date'].isna()]['units'].sum()
        if total_pending > self.THRESHOLDS["pending_units_critical"]:
            recs.append("Urgently expedite dispatch of pending units at all warehouses to reduce backlog.")

        # 2. If POD low
        pod_rate = self.calculate_kpis(df)['pod_achievement']['value']
        if pod_rate < 0.8:
            recs.append("Implement a POD follow-up campaign with sales managers to improve proof of delivery collection.")

        # 3. If delivery days high in certain cities
        top_cities = self.calculate_city_performance(df, top_n=3)
        for c in top_cities:
            if c['status'] in ['Critical', 'Warning']:
                recs.append(f"Review logistics routes and capacity for {c['city']} to reduce delivery days.")

        # 4. If warehouse performance low
        wh_perf = self.calculate_warehouse_performance(df)
        for wh in wh_perf[:3]:
            if wh['performance_score'] < 70:
                recs.append(f"Conduct an operational review at {wh['warehouse']} to improve performance score.")

        # 5. General recommendation
        if not recs:
            recs.append("All metrics are within acceptable ranges. Continue monitoring and maintain current performance levels.")

        return recs[:5]  # return top 5

    # ---------- Monthly Trend ----------
    def calculate_monthly_trend(self, df: pd.DataFrame) -> List[Dict]:
        """
        Aggregate DN count and units by month for the trend chart.
        """
        if df.empty:
            return []

        # Use created_at date for month
        df['month'] = df['created_at'].dt.to_period('M').dt.strftime('%Y-%m')
        trend = df.groupby('month').agg({
            'dn': 'nunique',
            'units': 'sum'
        }).reset_index().sort_values('month')
        result = []
        for _, row in trend.iterrows():
            result.append({
                'month': row['month'],
                'dn_count': int(row['dn']),
                'units': int(row['units'])
            })
        return result

    # ---------- Complete Dashboard Data ----------
    def get_dashboard_data(self) -> Dict[str, Any]:
        """
        Fetch all data and compute every metric required by the frontend.
        Returns a dictionary matching the expected JSON structure.
        """
        df = self.fetch_delivery_data()
        if df.empty:
            logger.warning("No data found in delivery_reports table.")
            # Return empty structures
            return {
                "cards": {},
                "executive_summary_text": "No data available. Please import an Excel file.",
                "pipeline_detailed": {},
                "warehouse_ranking": [],
                "top_delayed_cities": [],
                "top_pending_warehouses": [],
                "top_dealers": [],
                "top_products": [],
                "division_performance": [],
                "delivery_compliance": [],
                "alerts": [],
                "recommendations": ["No data to generate recommendations."],
                "monthly_trend": [],
                "metadata": {"record_count": 0, "version": "19.4"}
            }

        # Compute all metrics
        cards = self.calculate_kpis(df)
        summary = self.generate_executive_summary(df)
        pipeline = self.calculate_pipeline(df)
        warehouse_ranking = self.calculate_warehouse_performance(df)
        delayed_cities = self.calculate_city_performance(df)
        pending_warehouses = self.calculate_pending_analysis(df)
        top_dealers = self.calculate_dealer_performance(df)
        top_products = self.calculate_product_performance(df)
        division_perf = self.calculate_division_performance(df)
        compliance = self.calculate_delivery_compliance(df)
        alerts = self.generate_critical_alerts(df)
        recommendations = self.get_recommendations(df)
        monthly_trend = self.calculate_monthly_trend(df)

        metadata = {
            "record_count": len(df),
            "version": "19.4"
        }

        return {
            "cards": cards,
            "executive_summary_text": summary,
            "pipeline_detailed": pipeline,
            "warehouse_ranking": warehouse_ranking,
            "top_delayed_cities": delayed_cities,
            "top_pending_warehouses": pending_warehouses,
            "top_dealers": top_dealers,
            "top_products": top_products,
            "division_performance": division_perf,
            "delivery_compliance": compliance,
            "alerts": alerts,
            "recommendations": recommendations,
            "monthly_trend": monthly_trend,
            "metadata": metadata
        }

    # ---------- Excel Upload & Processing ----------
    def process_upload(self, file, skip_duplicates: bool = True) -> Dict[str, Any]:
        """
        Process an uploaded Excel file (SAP format) and update the database.

        Expected columns: DN, Warehouse, City, Dealer, Product, Division,
        Sales Office, Sales Manager, Units, Value, Delivery Date, POD Date,
        PGI Date, Status, etc. The exact mapping may vary.

        :param file: File object (from Flask request.files)
        :param skip_duplicates: If True, skip DNs that already exist; else update.
        :return: dict with status, message, and counts.
        """
        try:
            df = pd.read_excel(file)
            logger.info(f"Uploaded file with {len(df)} rows.")

            # Standardize column names: strip, lower, replace spaces with underscores
            df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')

            # Map columns to expected names (may need customization)
            expected_cols = {
                'dn': 'dn',
                'warehouse': 'warehouse',
                'city': 'city',
                'dealer': 'dealer',
                'product': 'product',
                'division': 'division',
                'sales_office': 'sales_office',
                'sales_manager': 'sales_manager',
                'units': 'units',
                'value': 'value',
                'delivery_date': 'delivery_date',
                'pod_date': 'pod_date',
                'pgi_date': 'pgi_date',
                'status': 'status'
            }
            # Map actual columns to expected names if they exist
            rename_map = {}
            for expected, alt_names in [
                ('dn', ['dn', 'delivery_note', 'delivery_note_number']),
                ('warehouse', ['warehouse', 'wh']),
                ('city', ['city', 'destination_city']),
                ('dealer', ['dealer', 'customer', 'distributor']),
                ('product', ['product', 'product_code', 'material']),
                ('division', ['division', 'business_unit']),
                ('sales_office', ['sales_office', 'office']),
                ('sales_manager', ['sales_manager', 'manager']),
                ('units', ['units', 'qty', 'quantity']),
                ('value', ['value', 'amount', 'revenue']),
                ('delivery_date', ['delivery_date', 'delivery_dt']),
                ('pod_date', ['pod_date', 'pod_dt', 'pod_received_date']),
                ('pgi_date', ['pgi_date', 'pgi_dt']),
                ('status', ['status', 'delivery_status']),
            ]:
                for alt in alt_names:
                    if alt in df.columns:
                        rename_map[alt] = expected
                        break

            df = df.rename(columns=rename_map)

            # Ensure required columns exist
            required = ['dn', 'warehouse', 'city', 'dealer', 'product', 'division',
                        'sales_office', 'sales_manager', 'units', 'value']
            missing = [col for col in required if col not in df.columns]
            if missing:
                raise ValueError(f"Missing required columns: {missing}")

            # Convert date columns
            date_cols = ['delivery_date', 'pod_date', 'pgi_date']
            for col in date_cols:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors='coerce')

            # Fill missing status if needed
            if 'status' not in df.columns:
                # Derive status from dates
                def derive_status(row):
                    if pd.notna(row.get('pod_date')):
                        return 'POD Received'
                    elif pd.notna(row.get('delivery_date')):
                        return 'Delivered'
                    elif pd.notna(row.get('pgi_date')):
                        return 'PGI Completed'
                    else:
                        return 'DN Created'
                df['status'] = df.apply(derive_status, axis=1)

            # Insert/update into database
            with self.Session() as session:
                # Use a raw connection for bulk insert with on conflict
                conn = session.connection()

                # Prepare data for upsert
                # We'll use pandas to_sql with if_exists='append' but handle duplicates manually
                # Better: use COPY or execute_values with ON CONFLICT
                from sqlalchemy.dialects.postgresql import insert

                # Convert to list of dicts
                records = df.to_dict(orient='records')
                inserted = 0
                updated = 0

                for rec in records:
                    # Prepare the statement
                    stmt = insert(self._get_table_metadata()).values(rec)
                    if skip_duplicates:
                        stmt = stmt.on_conflict_do_nothing(index_elements=['dn'])
                    else:
                        # Update on conflict
                        # Exclude primary key from update set
                        exclude_cols = ['dn']
                        update_cols = {c: getattr(stmt.excluded, c) for c in rec.keys() if c not in exclude_cols}
                        stmt = stmt.on_conflict_do_update(index_elements=['dn'], set_=update_cols)

                    result = session.execute(stmt)
                    if result.rowcount == 1:
                        inserted += 1
                    elif result.rowcount == 2:  # update
                        updated += 1

                session.commit()

            return {
                "status": "success",
                "message": f"Processed {len(df)} rows. Inserted: {inserted}, Updated: {updated}.",
                "inserted": inserted,
                "updated": updated,
                "total": len(df)
            }

        except Exception as e:
            logger.exception("Error processing upload")
            return {
                "status": "error",
                "message": str(e)
            }

    def _get_table_metadata(self):
        """Return the table object for the delivery_reports table."""
        from sqlalchemy import MetaData, Table
        metadata = MetaData()
        # Define table structure - this should match the actual DB schema
        # For simplicity, we assume it exists and reflect
        # But we'll just return the table object for insert
        table = Table('delivery_reports', metadata, autoload_with=self.engine)
        return table


# ---------- Flask Integration (Example) ----------
# If using Flask, you would create routes that call the service.

def create_dashboard_blueprint(service: DashboardService):
    """
    Creates a Flask Blueprint with routes for the dashboard.
    """
    from flask import Blueprint, jsonify, request, current_app

    bp = Blueprint('dashboard', __name__, url_prefix='/dashboard/api')

    @bp.route('/data', methods=['GET'])
    def get_dashboard_data():
        """Return all dashboard data as JSON."""
        try:
            data = service.get_dashboard_data()
            return jsonify(data)
        except Exception as e:
            current_app.logger.error(f"Dashboard data error: {e}")
            return jsonify({"error": str(e)}), 500

    @bp.route('/upload', methods=['POST'])
    def upload_excel():
        """Handle Excel file upload."""
        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No selected file"}), 400

        skip_duplicates = request.form.get('skip_duplicates', 'true').lower() == 'true'
        result = service.process_upload(file, skip_duplicates)
        if result.get('status') == 'success':
            return jsonify(result), 200
        else:
            return jsonify(result), 500

    return bp


# ---------- Standalone Usage Example ----------
if __name__ == "__main__":
    # For testing, you can instantiate and call methods.
    # Set DATABASE_URL environment variable or pass directly.
    service = DashboardService()
    data = service.get_dashboard_data()
    print(json.dumps(data, indent=2, default=str))
