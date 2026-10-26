from django.core.management.base import BaseCommand

from dashboard.basket_import import build_basket_data


class Command(BaseCommand):
    help = "Build exact basket/reorder summaries and frequent product pairs from original prior-order rows."

    def add_arguments(self, parser):
        parser.add_argument("--data-dir", required=True)
        parser.add_argument("--min-support", default="0.5", help="Minimum pair support as a percentage (default 0.5).")
        parser.add_argument("--min-orders", type=int, default=1000, help="Minimum distinct co-orders (default 1000).")

    def handle(self, *args, **options):
        snapshot = build_basket_data(options["data_dir"], options["min_support"], options["min_orders"], self.stdout.write)
        self.stdout.write(self.style.SUCCESS(
            f"Published {snapshot.order_count:,} baskets, {snapshot.pair_count:,} exact frequent pairs; support floor {snapshot.support_floor:,} orders."
        ))
