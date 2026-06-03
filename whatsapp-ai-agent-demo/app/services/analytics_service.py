# ==========================================================
# FILE: app/services/analytics_service.py
# ==========================================================

from datetime import date
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import DeliveryReport


class AnalyticsService:

    def __init__(self, db: Session):
        self.db = db

    # ======================================================
    # AGING CALCULATIONS
    # ======================================================

    @staticmethod
    def calculate_dispatch_age(record):
        """
        Dispatch Age = PGI Date - DN Create Date
        """

        if (
            not record.dn_create_date or
            not record.good_issue_date
        ):
            return None

        return (
            record.good_issue_date -
            record.dn_create_date
        ).days

    @staticmethod
    def calculate_pod_age(record):
        """
        POD Age = Today - PGI Date
        """

        if not record.good_issue_date:
            return None

        if record.pod_status == "Received":
            return None

        return (
            date.today() -
            record.good_issue_date
        ).days

    @staticmethod
    def calculate_delivery_cycle(record):
        """
        Delivery Cycle = POD Date - DN Create Date
        """

        if (
            not record.dn_create_date or
            not record.pod_date
        ):
            return None

        return (
            record.pod_date -
            record.dn_create_date
        ).days

    # ======================================================
    # EXECUTIVE METRICS
    # ======================================================

    def executive_metrics(self):

        records = self.db.query(
            DeliveryReport
        ).all()

        total_dns = len(
            set(
                r.dn_no
                for r in records
                if r.dn_no
            )
        )

        total_units = sum(
            r.dn_qty or 0
            for r in records
        )

        total_value = sum(
            r.dn_amount or 0
            for r in records
        )

        return {
            "total_dns": total_dns,
            "total_units": total_units,
            "total_value": total_value
        }

    # ======================================================
    # DEALER METRICS
    # ======================================================

    def dealer_metrics(
        self,
        dealer_name: str
    ):

        records = (
            self.db.query(
                DeliveryReport
            )
            .filter(
                DeliveryReport.customer_name
                == dealer_name
            )
            .all()
        )

        total_dns = len(
            set(
                r.dn_no
                for r in records
                if r.dn_no
            )
        )

        total_units = sum(
            r.dn_qty or 0
            for r in records
        )

        total_value = sum(
            r.dn_amount or 0
            for r in records
        )

        return {
            "dealer": dealer_name,
            "total_dns": total_dns,
            "total_units": total_units,
            "total_value": total_value
        }

    # ======================================================
    # PENDING METRICS
    # ======================================================

    def pending_metrics(
        self,
        dealer_name: str = None
    ):

        query = self.db.query(
            DeliveryReport
        )

        if dealer_name:

            query = query.filter(
                DeliveryReport.customer_name
                == dealer_name
            )

        records = query.filter(
            DeliveryReport.delivery_status
            != "Delivered"
        ).all()

        pending_dns = len(
            set(
                r.dn_no
                for r in records
                if r.dn_no
            )
        )

        pending_units = sum(
            r.dn_qty or 0
            for r in records
        )

        pending_value = sum(
            r.dn_amount or 0
            for r in records
        )

        return {
            "pending_dns": pending_dns,
            "pending_units": pending_units,
            "pending_value": pending_value
        }

    # ======================================================
    # POD METRICS
    # ======================================================

    def pod_metrics(
        self,
        dealer_name: str = None
    ):

        query = self.db.query(
            DeliveryReport
        )

        if dealer_name:

            query = query.filter(
                DeliveryReport.customer_name
                == dealer_name
            )

        records = query.filter(
            DeliveryReport.pod_status
            != "Received"
        ).all()

        pod_pending_dns = len(
            set(
                r.dn_no
                for r in records
                if r.dn_no
            )
        )

        pod_pending_units = sum(
            r.dn_qty or 0
            for r in records
        )

        pod_pending_value = sum(
            r.dn_amount or 0
            for r in records
        )

        oldest_pod_age = 0

        for record in records:

            age = self.calculate_pod_age(
                record
            )

            if age:

                oldest_pod_age = max(
                    oldest_pod_age,
                    age
                )

        return {
            "pod_pending_dns": pod_pending_dns,
            "pod_pending_units": pod_pending_units,
            "pod_pending_value": pod_pending_value,
            "oldest_pod_age": oldest_pod_age
        }

    # ======================================================
    # PRODUCT METRICS
    # ======================================================

    def product_metrics(
        self,
        dealer_name: str = None
    ):

        query = self.db.query(
            DeliveryReport
        )

        if dealer_name:

            query = query.filter(
                DeliveryReport.customer_name
                == dealer_name
            )

        records = query.all()

        products = defaultdict(
            lambda: {
                "total_qty": 0,
                "pending_qty": 0,
                "delivered_qty": 0,
                "value": 0
            }
        )

        for r in records:

            product = (
                r.material_no
                or "Unknown"
            )

            qty = r.dn_qty or 0

            products[product][
                "total_qty"
            ] += qty

            products[product][
                "value"
            ] += (
                r.dn_amount or 0
            )

            if (
                r.delivery_status
                == "Delivered"
            ):

                products[product][
                    "delivered_qty"
                ] += qty

            else:

                products[product][
                    "pending_qty"
                ] += qty

        return dict(products)

    # ======================================================
    # WAREHOUSE METRICS
    # ======================================================

    def warehouse_metrics(self):

        results = (
            self.db.query(
                DeliveryReport.warehouse,
                func.sum(
                    DeliveryReport.dn_qty
                ),
                func.sum(
                    DeliveryReport.dn_amount
                )
            )
            .group_by(
                DeliveryReport.warehouse
            )
            .all()
        )

        return results

    # ======================================================
    # CITY METRICS
    # ======================================================

    def city_metrics(self):

        results = (
            self.db.query(
                DeliveryReport.ship_to_city,
                func.sum(
                    DeliveryReport.dn_qty
                ),
                func.sum(
                    DeliveryReport.dn_amount
                )
            )
            .group_by(
                DeliveryReport.ship_to_city
            )
            .all()
        )

        return results
