"""
dashboard_service.py - Enterprise Logistics Dashboard Service for Haier Pakistan
Version: 19.4.1 – Robust, defensive, and fully aligned with frontend Command Center.
All calculations are centralized; handles missing columns and empty datasets gracefully.
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Any, Optional
from sqlalchemy import create_engine, text, MetaData, Table
from sqlalchemy.orm import sessionmaker
import os
import json
import traceback

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
        Initialize with database URL. Falls back to environment variable DATABASE_URL.
        """
        self.db_url = db_url or os.getenv("DATABASE_URL")
        if not self.db_url:
            logger.warning("No DATABASE_URL provided. Service will return empty data.")
            self.engine = None
        else:
            self.engine = create_engine(self.db_url)
        self.Session = sessionmaker(bind=self.engine) if self.engine else None

        # Thresholds
        self.THRESHOLDS = {
            "pgi_target": 0.95,
            "pod_target": 0.90,
            "delivery_days_warning": 5,
            "delivery_days_critical": 10,
            "pending_units_warning": 1000,
            "pending_units_critical": 5000,
            "health_score_bad": 70,
        }

    def _get_connection(self):
        """Return a raw DB connection for pandas read_sql."""
        if not self.engine:
            raise RuntimeError("Database engine not initialized.")
        return self.engine.connect()

    def _execute_query(self, query: str) -> pd.DataFrame:
        """Execute a SQL query and return a DataFrame. Handles empty results."""
        try:
            with self._get_connection() as conn:
                return pd.read_sql(text(query), conn)
        except Exception as e:
            logger.error(f"Database query failed: {e}")
            return pd.DataFrame()

    def fetch_delivery_data(self) -> pd.DataFrame:
        """
        Fetch all delivery records. Uses a safe query that works even if
        optional columns are missing.
        """
        # Select all columns that exist; we will check existence later.
        # Use a simple SELECT * but with error handling.
        try:
            # First, try to get column names from the table
            with self._get_connection() as conn:
                # Get column info
                inspector = pd.io.sql.get_schema(self.engine, 'delivery_reports')
                # simpler: use a query that returns one row to inspect columns
                sample_query = "SELECT * FROM delivery_reports LIMIT 1"
                sample = pd.read_sql(sample_query, conn)
                available_cols = sample.columns.tolist()
        except Exception as e:
            logger.error(f"Failed to fetch column info: {e}")
            return pd.DataFrame()

        # Build query: select all available columns, but we'll only use known ones
        query = "SELECT * FROM delivery_reports"
        # If 'deleted' column exists, filter it out
        if 'deleted' in available_cols:
            query += " WHERE deleted = false OR deleted IS NULL"
        df = self._execute_query(query)
        if df.empty:
            logger.warning("No data returned from delivery_reports.")
            return df

        # Convert date columns if they exist
        date_cols = [c for c in ['delivery_date', 'pod_date', 'pgi_date', 'created_at'] if c in df.columns]
        for col in date_cols:
            df[col] = pd.to_datetime(df[col], errors='coerce')

        # Ensure required columns exist; if missing, create with default values
        required_cols = ['dn', 'warehouse', 'city', 'dealer', 'product', 'division',
                         'sales_office', 'sales_manager', 'units', 'value']
        for col in required_cols:
            if col not in df.columns:
                logger.warning(f"Column '{col}' not found in DB. Creating with default 0/empty.")
                if col in ['units', 'value']:
                    df[col] = 0
                else:
                    df[col] = 'Unknown'

        # Fill NaN for numeric columns
        num_cols = ['units', 'value']
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # If dn is numeric, ensure it's string for nunique
        if 'dn' in df.columns:
            df['dn'] = df['dn'].astype(str)

        logger.info(f"Fetched {len(df)} rows with columns: {df.columns.tolist()}")
        return df

    # ---------- KPI Calculations ----------
    def calculate_kpis(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Compute the 8 executive KPI cards."""
        if df.empty:
            return {
                "total_dn": {"value": 0},
                "total_units": {"value": 0},
                "total_value": {"value": 0},
                "pgi_achievement": {"value": 0},
                "pod_achievement": {"value": 0},
                "pending_dn": {"value": 0},
                "pending_units": {"value": 0},
                "health_score": {"value": 0},
            }

        total_dn = df['dn'].nunique()
        total_units = df['units'].sum()
        total_value = df['value'].sum()

        # PGI: use pgi_date if exists, else assume all are PGI completed (fallback)
        if 'pgi_date' in df.columns:
            pgi_count = df['pgi_date'].notna().sum()
        else:
            pgi_count = total_dn  # assume all are PGI if column missing
        pgi_achievement = pgi_count / total_dn if total_dn > 0 else 0

        # POD
        if 'pod_date' in df.columns:
            pod_count = df['pod_date'].notna().sum()
        else:
            pod_count = 0
        pod_achievement = pod_count / total_dn if total_dn > 0 else 0

        # Pending: based on delivery_date
        if 'delivery_date' in df.columns:
            pending_df = df[df['delivery_date'].isna()]
            pending_dn = pending_df['dn'].nunique()
            pending_units = pending_df['units'].sum()
        else:
            pending_dn = 0
            pending_units = 0

        # Health score: weighted average of PGI and POD, penalty for pending
        health_score = (pgi_achievement * 0.4 + pod_achievement * 0.4) * 100
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
        if df.empty:
            return "No data available. Please import an Excel file to see metrics."

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

        # Add insight on top delayed cities if possible
        top_cities = self.calculate_city_performance(df, top_n=3)
        if top_cities:
            city_names = ", ".join([c['city'] for c in top_cities])
            summary += f" Top delayed cities: {city_names}."
        return summary

    # ---------- Pipeline (Today) ----------
    def calculate_pipeline(self, df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
        if df.empty:
            return {
                "dn_created": {"dn": 0, "pct": 0},
                "pgi_completed": {"dn": 0, "pct": 0},
                "in_transit": {"dn": 0, "pct": 0},
                "delivered": {"dn": 0, "pct": 0},
                "pod_received": {"dn": 0, "pct": 0},
            }

        # Use created_at if available, else assume all are today's
        if 'created_at' in df.columns:
            today = datetime.now().date()
            today_df = df[df['created_at'].dt.date == today]
        else:
            today_df = df

        total_dn_today = today_df['dn'].nunique() if not today_df.empty else 1
        if total_dn_today == 0:
            total_dn_today = 1

        dn_created = total_dn_today
        # PGI: count non-null pgi_date
        if 'pgi_date' in today_df.columns:
            pgi_completed = today_df['pgi_date'].notna().sum()
        else:
            pgi_completed = 0

        # In transit: pgi done, delivery not done
        if 'pgi_date' in today_df.columns and 'delivery_date' in today_df.columns:
            in_transit = today_df[(today_df['pgi_date'].notna()) & (today_df['delivery_date'].isna())]['dn'].nunique()
        else:
            in_transit = 0

        if 'delivery_date' in today_df.columns:
            delivered = today_df[today_df['delivery_date'].notna()]['dn'].nunique()
        else:
            delivered = 0

        if 'pod_date' in today_df.columns:
            pod_received = today_df[today_df['pod_date'].notna()]['dn'].nunique()
        else:
            pod_received = 0

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
        if df.empty:
            return []

        # Group by warehouse
        agg_dict = {
            'dn': 'nunique',
            'units': 'sum',
            'value': 'sum',
        }
        if 'pgi_date' in df.columns:
            agg_dict['pgi_date'] = lambda x: x.notna().sum()
        if 'pod_date' in df.columns:
            agg_dict['pod_date'] = lambda x: x.notna().sum()
        if 'delivery_date' in df.columns:
            agg_dict['delivery_date'] = lambda x: x.notna().sum()

        grouped = df.groupby('warehouse').agg(agg_dict).reset_index()

        # Ensure columns exist
        for col in ['dn', 'units', 'value']:
            if col not in grouped.columns:
                grouped[col] = 0
        grouped.rename(columns={'dn': 'dns'}, inplace=True)

        # Compute pct columns if available
        if 'pgi_date' in grouped.columns:
            grouped['pgi_pct'] = grouped['pgi_date'] / grouped['dns'] * 100
        else:
            grouped['pgi_pct'] = 100.0  # assume all PGI

        if 'delivery_date' in grouped.columns:
            grouped['delivery_pct'] = grouped['delivery_date'] / grouped['dns'] * 100
        else:
            grouped['delivery_pct'] = 0.0

        if 'pod_date' in grouped.columns:
            grouped['pod_pct'] = grouped['pod_date'] / grouped['dns'] * 100
        else:
            grouped['pod_pct'] = 0.0

        # Pending units: sum of units where delivery_date is null
        if 'delivery_date' in df.columns:
            pending_df = df[df['delivery_date'].isna()].groupby('warehouse').agg({
                'units': 'sum',
                'dn': 'nunique'
            }).rename(columns={'units': 'pending_units', 'dn': 'pending_dns'}).reset_index()
            grouped = grouped.merge(pending_df, on='warehouse', how='left')
            grouped['pending_units'] = grouped['pending_units'].fillna(0)
            grouped['pending_dns'] = grouped['pending_dns'].fillna(0)
        else:
            grouped['pending_units'] = 0
            grouped['pending_dns'] = 0

        # Avg delivery days: delivery_date - pgi_date
        if 'delivery_date' in df.columns and 'pgi_date' in df.columns:
            delivered_df = df[df['delivery_date'].notna()].copy()
            if not delivered_df.empty:
                delivered_df['delivery_days'] = (delivered_df['delivery_date'] - delivered_df['pgi_date']).dt.days
                avg_delivery = delivered_df.groupby('warehouse')['delivery_days'].mean().fillna(0)
                grouped = grouped.merge(avg_delivery, on='warehouse', how='left')
                grouped.rename(columns={'delivery_days': 'avg_delivery_days'}, inplace=True)
            else:
                grouped['avg_delivery_days'] = 0
        else:
            grouped['avg_delivery_days'] = 0

        # Avg POD days: pod_date - delivery_date
        if 'pod_date' in df.columns and 'delivery_date' in df.columns:
            pod_df = df[df['pod_date'].notna()].copy()
            if not pod_df.empty:
                pod_df['pod_days'] = (pod_df['pod_date'] - pod_df['delivery_date']).dt.days
                avg_pod = pod_df.groupby('warehouse')['pod_days'].mean().fillna(0)
                grouped = grouped.merge(avg_pod, on='warehouse', how='left')
                grouped.rename(columns={'pod_days': 'avg_pod_days'}, inplace=True)
            else:
                grouped['avg_pod_days'] = 0
        else:
            grouped['avg_pod_days'] = 0

        # Performance score
        grouped['performance_score'] = (
            grouped['pgi_pct'] * 0.3 +
            grouped['delivery_pct'] * 0.3 +
            grouped['pod_pct'] * 0.3
        )
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

        # Trend
        avg_score = grouped['performance_score'].mean()
        grouped['trend'] = grouped['performance_score'].apply(
            lambda x: "↑" if x > avg_score else ("↓" if x < avg_score else "▬")
        )

        # AI Insight
        def ai_insight(row):
            if row['pending_units'] > self.THRESHOLDS["pending_units_critical"]:
                return "High pending units. Immediate action required."
            elif row['avg_delivery_days'] > self.THRESHOLDS["delivery_days_warning"]:
                return f"Avg delivery {row['avg_delivery_days']:.1f} days. Optimize routes."
            elif row['pod_pct'] < 80:
                return "Low POD rate. Follow up on proof of delivery."
            else:
                return "Good performance. Maintain standards."

        grouped['ai_insight'] = grouped.apply(ai_insight, axis=1)

        # Sort and rank
        grouped = grouped.sort_values('performance_score', ascending=False)
        grouped['rank'] = range(1, len(grouped) + 1)

        # Select columns
        cols = ['rank', 'warehouse', 'performance_score', 'dns', 'units', 'value',
                'pgi_pct', 'delivery_pct', 'pod_pct', 'avg_delivery_days',
                'avg_pod_days', 'pending_units', 'risk', 'trend', 'ai_insight']
        for c in cols:
            if c not in grouped.columns:
                grouped[c] = 0

        result = grouped[cols].to_dict(orient='records')
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
        if df.empty:
            return []

        if 'delivery_date' not in df.columns or 'pgi_date' not in df.columns:
            return []

        delivered = df[df['delivery_date'].notna()].copy()
        if delivered.empty:
            return []

        delivered['delivery_days'] = (delivered['delivery_date'] - delivered['pgi_date']).dt.days

        city_agg = delivered.groupby('city').agg({
            'delivery_days': 'mean',
            'dn': 'nunique',
            'units': 'sum'
        }).rename(columns={'delivery_days': 'avg_delivery_days'})

        # Pending units per city
        pending = df[df['delivery_date'].isna()].groupby('city')['units'].sum().rename('pending_units')
        city_agg = city_agg.merge(pending, on='city', how='left')
        city_agg['pending_units'] = city_agg['pending_units'].fillna(0)

        def status(row):
            if row['avg_delivery_days'] > self.THRESHOLDS["delivery_days_critical"]:
                return "Critical"
            elif row['avg_delivery_days'] > self.THRESHOLDS["delivery_days_warning"]:
                return "Warning"
            else:
                return "Good"

        city_agg['status'] = city_agg.apply(status, axis=1)
        city_agg = city_agg.sort_values('avg_delivery_days', ascending=False).head(top_n)

        result = []
        for city, row in city_agg.iterrows():
            result.append({
                'city': city,
                'avg_delivery_days': round(row['avg_delivery_days'], 1),
                'pending_units': int(row['pending_units']),
                'status': row['status']
            })
        return result

    # ---------- Top Pending Warehouses ----------
    def calculate_pending_analysis(self, df: pd.DataFrame, top_n: int = 5) -> List[Dict]:
        if df.empty or 'delivery_date' not in df.columns:
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
        if df.empty or 'delivery_date' not in df.columns or 'pgi_date' not in df.columns:
            return []

        delivered = df[df['delivery_date'].notna()].copy()
        if delivered.empty:
            return []

        delivered['delivery_days'] = (delivered['delivery_date'] - delivered['pgi_date']).dt.days
        delivered = delivered[delivered['delivery_days'] >= 0]

        if delivered.empty:
            return []

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
        alerts = []

        if df.empty:
            return alerts

        # 1. Pending units per warehouse
        if 'delivery_date' in df.columns:
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

        # 2. Delivery days per city
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
        if 'pod_date' in df.columns:
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

        # 4. Health score
        health = self.calculate_kpis(df)['health_score']['value']
        if health < self.THRESHOLDS["health_score_bad"]:
            alerts.append({
                "category": "Health Score",
                "source": "Overall Logistics",
                "message": f"Overall health score is {health:.1f}%, below acceptable level.",
                "severity": "CRITICAL"
            })

        alerts.sort(key=lambda x: 0 if x['severity'] == 'CRITICAL' else 1)
        return alerts[:10]

    # ---------- Director Recommendations ----------
    def get_recommendations(self, df: pd.DataFrame) -> List[str]:
        recs = []
        if df.empty:
            return ["No data to generate recommendations. Please import an Excel file."]

        total_pending = df[df['delivery_date'].isna()]['units'].sum() if 'delivery_date' in df.columns else 0
        if total_pending > self.THRESHOLDS["pending_units_critical"]:
            recs.append("Urgently expedite dispatch of pending units at all warehouses to reduce backlog.")

        pod_rate = self.calculate_kpis(df)['pod_achievement']['value']
        if pod_rate < 0.8:
            recs.append("Implement a POD follow-up campaign with sales managers to improve proof of delivery collection.")

        top_cities = self.calculate_city_performance(df, top_n=3)
        for c in top_cities:
            if c['status'] in ['Critical', 'Warning']:
                recs.append(f"Review logistics routes and capacity for {c['city']} to reduce delivery days.")

        wh_perf = self.calculate_warehouse_performance(df)
        for wh in wh_perf[:3]:
            if wh['performance_score'] < 70:
                recs.append(f"Conduct an operational review at {wh['warehouse']} to improve performance score.")

        if not recs:
            recs.append("All metrics are within acceptable ranges. Continue monitoring and maintain current performance levels.")

        return recs[:5]

    # ---------- Monthly Trend ----------
    def calculate_monthly_trend(self, df: pd.DataFrame) -> List[Dict]:
        if df.empty:
            return []

        # Use created_at or fallback to delivery_date
        if 'created_at' in df.columns:
            df['month'] = df['created_at'].dt.to_period('M').dt.strftime('%Y-%m')
        elif 'delivery_date' in df.columns:
            df['month'] = df['delivery_date'].dt.to_period('M').dt.strftime('%Y-%m')
        else:
            return []

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
        try:
            df = self.fetch_delivery_data()
            if df.empty:
                logger.warning("No data found in delivery_reports table. Returning empty structures.")
                return {
                    "cards": {
                        "total_dn": {"value": 0},
                        "total_units": {"value": 0},
                        "total_value": {"value": 0},
                        "pgi_achievement": {"value": 0},
                        "pod_achievement": {"value": 0},
                        "pending_dn": {"value": 0},
                        "pending_units": {"value": 0},
                        "health_score": {"value": 0},
                    },
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
                    "metadata": {"record_count": 0, "version": "19.4.1"}
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
                "version": "19.4.1"
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

        except Exception as e:
            logger.error(f"Dashboard data generation failed: {traceback.format_exc()}")
            # Return a friendly error structure that the frontend can display
            return {
                "cards": {},
                "executive_summary_text": f"Error loading dashboard data: {str(e)}",
                "pipeline_detailed": {},
                "warehouse_ranking": [],
                "top_delayed_cities": [],
                "top_pending_warehouses": [],
                "top_dealers": [],
                "top_products": [],
                "division_performance": [],
                "delivery_compliance": [],
                "alerts": [],
                "recommendations": ["Unable to generate recommendations due to an error."],
                "monthly_trend": [],
                "metadata": {"record_count": 0, "version": "19.4.1", "error": str(e)}
            }

    # ---------- Excel Upload & Processing ----------
    def process_upload(self, file, skip_duplicates: bool = True) -> Dict[str, Any]:
        """
        Process an uploaded Excel file and update the database.
        """
        try:
            df = pd.read_excel(file)
            logger.info(f"Uploaded file with {len(df)} rows.")

            # Clean column names
            df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')

            # Define mapping
            rename_map = {}
            for expected, alts in [
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
                for alt in alts:
                    if alt in df.columns:
                        rename_map[alt] = expected
                        break

            df = df.rename(columns=rename_map)

            # Ensure required columns
            required = ['dn', 'warehouse', 'city', 'dealer', 'product', 'division',
                        'sales_office', 'sales_manager', 'units', 'value']
            missing = [c for c in required if c not in df.columns]
            if missing:
                raise ValueError(f"Missing required columns: {missing}")

            # Convert dates
            date_cols = ['delivery_date', 'pod_date', 'pgi_date']
            for col in date_cols:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors='coerce')

            # Derive status if missing
            if 'status' not in df.columns:
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

            # Upsert logic
            if not self.engine:
                return {"status": "error", "message": "Database engine not initialized."}

            with self.Session() as session:
                conn = session.connection()
                from sqlalchemy.dialects.postgresql import insert

                table = Table('delivery_reports', MetaData(), autoload_with=self.engine)
                records = df.to_dict(orient='records')
                inserted = 0
                updated = 0

                for rec in records:
                    stmt = insert(table).values(rec)
                    if skip_duplicates:
                        stmt = stmt.on_conflict_do_nothing(index_elements=['dn'])
                    else:
                        exclude_cols = ['dn']
                        update_cols = {c: getattr(stmt.excluded, c) for c in rec.keys() if c not in exclude_cols}
                        stmt = stmt.on_conflict_do_update(index_elements=['dn'], set_=update_cols)

                    result = session.execute(stmt)
                    if result.rowcount == 1:
                        inserted += 1
                    elif result.rowcount == 2:
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


# ---------- Flask Blueprint (Optional) ----------
def create_dashboard_blueprint(service: DashboardService):
    from flask import Blueprint, jsonify, request, current_app

    bp = Blueprint('dashboard', __name__, url_prefix='/dashboard/api')

    @bp.route('/data', methods=['GET'])
    def get_dashboard_data():
        try:
            data = service.get_dashboard_data()
            return jsonify(data)
        except Exception as e:
            current_app.logger.error(f"Dashboard data error: {traceback.format_exc()}")
            return jsonify({"error": str(e)}), 500

    @bp.route('/upload', methods=['POST'])
    def upload_excel():
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
