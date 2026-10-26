"""Exact two-pass basket aggregation, with bounded sparse counters on disk."""
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from itertools import combinations, islice
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management.base import CommandError
from django.db import transaction

from .models import Aisle, BasketPair, BasketSize, BasketSnapshot, Department, Product
from .product_import import positive_id, rows


def catalog_signature():
    digest = hashlib.sha256()
    for model, fields in (
        (Product, ("product_id", "name", "aisle_id", "department_id")),
        (Aisle, ("aisle_id", "name")),
        (Department, ("department_id", "name")),
    ):
        for row in model.objects.order_by("pk").values_list(*fields).iterator(chunk_size=2000):
            digest.update(json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n")
    return digest.hexdigest()


def validate_catalog(root):
    products = dict(Product.objects.values_list("product_id", "aisle_id"))
    if not products:
        raise CommandError("Import the product catalog first using import_products.")
    for filename, model, key, columns, db_fields in (
        ("products.csv", Product, "product_id", ("product_name", "aisle_id", "department_id"), ("name", "aisle_id", "department_id")),
        ("aisles.csv", Aisle, "aisle_id", ("aisle",), ("name",)),
        ("departments.csv", Department, "department_id", ("department",), ("name",)),
    ):
        expected = {row[0]: tuple(str(value) for value in row[1:]) for row in model.objects.values_list(key, *db_fields)}
        seen = set()
        with rows(root / filename, (key, *columns)) as reader:
            for row in reader:
                pk = positive_id(row[key])
                if pk in seen or expected.get(pk) != tuple(row[column] for column in columns):
                    raise CommandError(f"{filename}: source catalog differs from existing product relationships at {pk}.")
                seen.add(pk)
        if seen != set(expected):
            raise CommandError(f"{filename}: source and imported catalog IDs differ.")
    return set(products)


def iter_baskets(path, known_products, digest):
    """Yield one distinct-membership basket and row-level reorder coverage at a time."""
    last_order = None
    members = set()
    occurrences = valid = reordered = 0
    try:
        with path.open("rb") as raw:
            # Hash the exact byte stream in both passes without another file scan.
            def lines():
                for index, line in enumerate(raw):
                    digest.update(line)
                    yield line.decode("utf-8-sig" if index == 0 else "utf-8")
            reader = csv.reader(lines())
            header = next(reader, [])
            required = ("order_id", "product_id", "reordered")
            if not set(required).issubset(header):
                raise CommandError("order_products__prior.csv: missing required columns")
            oi, pi, ri = (header.index(name) for name in required)
            for line_number, row in enumerate(reader, start=2):
                if len(row) != len(header):
                    raise CommandError(f"Malformed order-product row {line_number}")
                order_id, product_id = positive_id(row[oi]), positive_id(row[pi])
                if product_id not in known_products:
                    raise CommandError(f"Unknown product {product_id} at row {line_number}")
                if last_order is not None and order_id < last_order:
                    raise CommandError("Order-product rows must be sorted by order_id; existing snapshot preserved.")
                if last_order is not None and order_id != last_order:
                    yield members, occurrences, valid, reordered
                    members = set()
                    occurrences = valid = reordered = 0
                last_order = order_id
                members.add(product_id)
                occurrences += 1
                if row[ri] in ("0", "1"):
                    valid += 1
                    reordered += row[ri] == "1"
            if last_order is not None:
                yield members, occurrences, valid, reordered
    except (OSError, UnicodeError, csv.Error) as exc:
        raise CommandError(f"Cannot read {path}: {exc}") from exc


def build_basket_data(data_dir, min_support_pct="0.5", min_orders=1000,
                      progress=lambda message: None, counter_limit=100000):
    try:
        support_pct = Decimal(str(min_support_pct))
        if not support_pct.is_finite() or not 0 < support_pct <= 100 or min_orders < 1 or counter_limit < 1:
            raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        raise CommandError("Minimum support must be > 0 and <= 100%; minimum orders and counter limit must be positive.")
    root = Path(data_dir)
    known = validate_catalog(root)
    signature = catalog_signature()
    source = root / "order_products__prior.csv"
    marginals = Counter()
    # Histogram fields: orders, source rows, valid indicators, reordered rows.
    histogram = defaultdict(lambda: [0, 0, 0, 0])
    total_orders = total_rows = total_memberships = total_valid = total_reorders = 0
    first_digest = hashlib.sha256()
    for members, occurrences, valid, reordered in iter_baskets(source, known, first_digest):
        marginals.update(members)
        bucket = histogram[len(members)]
        for index, value in enumerate((1, occurrences, valid, reordered)):
            bucket[index] += value
        total_orders += 1
        total_rows += occurrences
        total_memberships += len(members)
        total_valid += valid
        total_reorders += reordered
        if total_orders % 500000 == 0:
            progress(f"Pass 1: {total_orders:,} baskets...")
    if not total_orders:
        raise CommandError("No nonempty baskets found; existing snapshot preserved.")
    floor = max(min_orders, int((Decimal(total_orders) * support_pct / 100).to_integral_value(rounding=ROUND_CEILING)))
    eligible = {pk for pk, count in marginals.items() if count >= floor}
    progress(f"Pair floor: {floor:,} orders; {len(eligible):,} eligible products. Counting exact sparse pairs...")

    with TemporaryDirectory(prefix="instacart-basket-") as scratch:
        store = sqlite3.connect(Path(scratch) / "pairs.sqlite3")
        try:
            # This disposable store is never the application database.
            store.execute("PRAGMA journal_mode=OFF")
            store.execute("PRAGMA cache_size=-32768")
            store.execute("CREATE TABLE pairs (a INTEGER, b INTEGER, n INTEGER, PRIMARY KEY(a,b)) WITHOUT ROWID")
            pending = Counter()
            def flush():
                store.executemany("INSERT INTO pairs VALUES (?,?,?) ON CONFLICT(a,b) DO UPDATE SET n=n+excluded.n",
                                  ((a, b, n) for (a, b), n in sorted(pending.items())))
                pending.clear()
            second_digest = hashlib.sha256()
            for count, (members, _, _, _) in enumerate(iter_baskets(source, known, second_digest), start=1):
                for pair in combinations(sorted(members.intersection(eligible)), 2):
                    pending[pair] += 1
                    if len(pending) >= counter_limit:
                        flush()
                if count % 500000 == 0:
                    progress(f"Pass 2: {count:,} baskets...")
            flush()
            store.commit()
            if first_digest.digest() != second_digest.digest() or catalog_signature() != signature:
                raise CommandError("Source or product catalog changed during processing; existing snapshot preserved.")
            pair_count = store.execute("SELECT COUNT(*) FROM pairs WHERE n >= ?", (floor,)).fetchone()[0]
            with transaction.atomic():
                BasketPair.objects.all().delete()
                BasketSize.objects.all().delete()
                BasketSize.objects.bulk_create([
                    BasketSize(product_count=size, order_count=values[0], occurrences=values[1],
                               valid_reorder_count=values[2], reorder_count=values[3])
                    for size, values in sorted(histogram.items())
                ], batch_size=500)
                def records():
                    for a, b, n in store.execute("SELECT a,b,n FROM pairs WHERE n >= ? ORDER BY a,b", (floor,)):
                        yield BasketPair(product_a_id=a, product_b_id=b, order_count=n,
                            a_order_count=marginals[a], b_order_count=marginals[b],
                            support=n * 100 / total_orders,
                            confidence_a=n * 100 / marginals[a], confidence_b=n * 100 / marginals[b],
                            lift=n * total_orders / (marginals[a] * marginals[b]))
                remaining = iter(records())
                while batch := list(islice(remaining, 500)):
                    BasketPair.objects.bulk_create(batch, batch_size=500)
                snapshot, _ = BasketSnapshot.objects.update_or_create(pk=1, defaults={
                    "source_name": source.name, "catalog_signature": signature,
                    "source_sha256": first_digest.hexdigest(), "order_count": total_orders,
                    "occurrences": total_rows, "memberships": total_memberships,
                    "reorder_count": total_reorders, "valid_reorder_count": total_valid,
                    "product_count": len(marginals), "pair_count": pair_count, "support_floor": floor,
                    "requested_support_pct": float(support_pct), "requested_min_orders": min_orders,
                })
            return snapshot
        except sqlite3.Error as exc:
            raise CommandError(f"Temporary pair store failed; snapshot preserved: {exc}") from exc
        finally:
            store.close()
