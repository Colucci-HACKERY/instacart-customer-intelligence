"""Bounded-memory aggregation of canonical, order-ID-sorted Instacart prior CSVs.

No writes occur until every source row has passed structural validation.
Category order counts use per-order sets, never sums of product order counts.
"""
import csv
from contextlib import contextmanager
from pathlib import Path

from django.core.management.base import CommandError
from django.db import transaction

from .models import Aisle, Department, Product, ProductImport


@contextmanager
def rows(path, required):
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if not set(required).issubset(reader.fieldnames or []):
                raise CommandError(f"{path.name}: required columns are {', '.join(required)}")
            yield reader
    except (OSError, UnicodeError, csv.Error) as exc:
        raise CommandError(f"Cannot read {path}: {exc}") from exc


def positive_id(value):
    try:
        number = int(value)
        if number < 1 or number > 2147483647:
            raise ValueError
        return number
    except (TypeError, ValueError):
        raise CommandError(f"Invalid source ID: {value!r}")


def load_dimension(path, model, key, name):
    result = {}
    with rows(path, (key, name)) as reader:
        for row in reader:
            pk = positive_id(row[key])
            if pk in result or not row[name] or len(row[name]) > 200:
                raise CommandError(f"{path.name}: duplicate ID or invalid name at {pk}")
            result[pk] = model(pk=pk, name=row[name])
    if not result:
        raise CommandError(f"{path.name}: dimension is empty")
    return result


def import_product_data(data_dir, progress=lambda message: None):
    root = Path(data_dir)
    departments = load_dimension(root / "departments.csv", Department, "department_id", "department")
    aisles = load_dimension(root / "aisles.csv", Aisle, "aisle_id", "aisle")
    products = {}
    with rows(root / "products.csv", ("product_id", "product_name", "aisle_id", "department_id")) as reader:
        for row in reader:
            pk = positive_id(row["product_id"])
            aisle_id = positive_id(row["aisle_id"])
            department_id = positive_id(row["department_id"])
            if pk in products or aisle_id not in aisles or department_id not in departments:
                raise CommandError(f"products.csv: duplicate or broken relationship at product {pk}")
            if not row["product_name"] or len(row["product_name"]) > 500:
                raise CommandError(f"products.csv: invalid product name at {pk}")
            products[pk] = Product(pk=pk, name=row["product_name"], aisle_id=aisle_id, department_id=department_id)
    if not products:
        raise CommandError("products.csv: product catalog is empty")

    last_order = None
    seen_products, seen_aisles, seen_departments = set(), set(), set()
    total_orders = total_rows = 0

    def finish_order():
        for pk in seen_products:
            products[pk].order_count += 1
        for pk in seen_aisles:
            aisles[pk].order_count += 1
        for pk in seen_departments:
            departments[pk].order_count += 1
        seen_products.clear()
        seen_aisles.clear()
        seen_departments.clear()

    source_path = root / "order_products__prior.csv"
    # csv.reader avoids constructing 32 million temporary dictionaries.
    try:
        with source_path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source)
            header = next(reader, [])
            required = ("order_id", "product_id", "add_to_cart_order", "reordered")
            if not set(required).issubset(header):
                raise CommandError(f"{source_path.name}: missing required columns")
            oi, pi, ri = (header.index(name) for name in ("order_id", "product_id", "reordered"))
            for line, row in enumerate(reader, start=2):
                if len(row) != len(header):
                    raise CommandError(f"{source_path.name}: malformed row {line}")
                order_id, product_id = positive_id(row[oi]), positive_id(row[pi])
                if last_order is not None and order_id < last_order:
                    raise CommandError(f"{source_path.name}: rows must be sorted by order_id; row {line} is out of order. Existing snapshot preserved.")
                if order_id != last_order:
                    finish_order()
                    total_orders += 1
                    last_order = order_id
                product = products.get(product_id)
                if product is None:
                    raise CommandError(f"Unknown product {product_id} at row {line}")
                seen_products.add(product_id)
                seen_aisles.add(product.aisle_id)
                seen_departments.add(product.department_id)
                product.occurrences += 1
                if row[ri] in ("0", "1"):
                    product.valid_reorder_count += 1
                    product.reorder_count += row[ri] == "1"
                total_rows += 1
                if total_rows % 2000000 == 0:
                    progress(f"Read {total_rows:,} product occurrences...")
            finish_order()
    except (OSError, UnicodeError, csv.Error) as exc:
        raise CommandError(f"Cannot read {source_path}: {exc}") from exc
    if not total_rows:
        raise CommandError("No order-product rows found; existing snapshot preserved")

    for product in products.values():
        for category in (aisles[product.aisle_id], departments[product.department_id]):
            category.occurrences += product.occurrences
            category.valid_reorder_count += product.valid_reorder_count
            category.reorder_count += product.reorder_count
    for group in (products, aisles, departments):
        for record in group.values():
            record.reorder_rate = record.reorder_count * 100 / record.valid_reorder_count if record.valid_reorder_count else None

    # A short atomic publication replaces only these derived product summaries.
    # No existing customer/order model is written, and failed publication rolls back.
    with transaction.atomic():
        Product.objects.all().delete()
        Aisle.objects.all().delete()
        Department.objects.all().delete()
        Department.objects.bulk_create(departments.values(), batch_size=500)
        Aisle.objects.bulk_create(aisles.values(), batch_size=500)
        Product.objects.bulk_create(products.values(), batch_size=500)
        snapshot, _ = ProductImport.objects.update_or_create(pk=1, defaults={
            "source_name": source_path.name,
            "order_count": total_orders,
            "occurrences": total_rows,
            "valid_reorder_count": sum(p.valid_reorder_count for p in products.values()),
            "reorder_count": sum(p.reorder_count for p in products.values()),
            "product_count": len(products),
        })
    return snapshot
