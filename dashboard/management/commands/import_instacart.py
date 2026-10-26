from pathlib import Path
import pandas as pd
from django.core.management.base import BaseCommand
from dashboard.models import InstacartOrder
from django.core.cache import cache

DATA_PATH = Path(
    r"C:\Users\akany\OneDrive - Botswana Accountancy College"
    r"\Documents\ADA\ADA2026_Instacart_Project"
    r"\data\processed\t05_engineered_data.csv"
)


class Command(BaseCommand):

    help = "Import processed Instacart orders into the Django database"

    def handle(self, *args, **options):

        self.stdout.write(
            self.style.WARNING(
                "Starting Instacart data import..."
            )
        )

        if not DATA_PATH.exists():

            self.stdout.write(
                self.style.ERROR(
                    f"Dataset not found: {DATA_PATH}"
                )
            )

            return

        self.stdout.write(
            f"Reading dataset:\n{DATA_PATH}"
        )

        df = pd.read_csv(DATA_PATH)

        self.stdout.write(
            f"Rows found: {len(df):,}"
        )

        # Remove existing records so the command
        # can safely be run again.
        # Remove cached Overview figures before changing the dataset.
        cache.delete("instacart_overview_analytics_v1")
        InstacartOrder.objects.all().delete()

        self.stdout.write(
            "Existing database records cleared."
        )

        records = []

        for row in df.itertuples(index=False):

            records.append(
    InstacartOrder(
        order_id=int(row.order_id),

        user_id=int(row.user_id),

        order_dow=int(row.order_dow),

        days_since_prior_order=(
            None
            if pd.isna(row.days_since_prior_order)
            else float(row.days_since_prior_order)
        ),

        order_size=(
            None
            if pd.isna(row.order_size)
            else float(row.order_size)
        ),
    )
)

            if len(records) >= 5000:

                InstacartOrder.objects.bulk_create(
                    records,
                    batch_size=5000
                )

                records = []

        if records:

            InstacartOrder.objects.bulk_create(
                records,
                batch_size=5000
            )

        total = InstacartOrder.objects.count()

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete: {total:,} records"
            )
        )