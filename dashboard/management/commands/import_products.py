from django.core.management.base import BaseCommand

from dashboard.product_import import import_product_data


class Command(BaseCommand):
    help = "Stream original Instacart prior-order CSVs into product demand summaries; preserve customer and order tables."

    def add_arguments(self, parser):
        parser.add_argument("--data-dir", required=True, help="Directory containing original products.csv, aisles.csv, departments.csv and order_products__prior.csv")

    def handle(self, *args, **options):
        snapshot = import_product_data(options["data_dir"], self.stdout.write)
        self.stdout.write(self.style.SUCCESS(
            f"Published {snapshot.product_count:,} products; {snapshot.order_count:,} distinct prior orders; {snapshot.occurrences:,} occurrences."
        ))
