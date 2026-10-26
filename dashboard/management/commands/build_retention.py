from django.core.management.base import BaseCommand

from dashboard.retention_build import build_retention


class Command(BaseCommand):
    help = "Stream original orders.csv into a separate versioned historical-cadence artifact. No application records are written."

    def add_arguments(self, parser):
        parser.add_argument("--orders-csv", required=True, help="Path to original orders.csv, including order_number.")
        parser.add_argument("--output", help="Optional artifact path; defaults to artifacts/retention-v1.sqlite3.")

    def handle(self, *args, **options):
        summary = build_retention(options["orders_csv"], options["output"], self.stdout.write)
        self.stdout.write(self.style.SUCCESS(
            f"Published v{summary['format_version']}: {summary['source_customers']:,} customers, {summary['valid_gaps']:,} valid completed gaps, {summary['invalid_sequence_customers']:,} invalid sequences excluded."
        ))
