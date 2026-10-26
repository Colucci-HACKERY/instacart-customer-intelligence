"""Read compact published aggregates; never scan source order-product CSVs on requests."""
from django.db.models import F, Q

from .models import Aisle, Department, Product, ProductImport


SORTS = {
    "orders": ["-order_count", "product_id"],
    "occurrences": ["-occurrences", "product_id"],
    "reorders": ["-reorder_count", "product_id"],
    "reorder_rate": [F("reorder_rate").desc(nulls_last=True), "-valid_reorder_count", "product_id"],
    "name": ["name", "product_id"],
}


def product_directory(cleaned):
    products = Product.objects.select_related("aisle", "department")
    query = cleaned.get("q", "")
    if query:
        predicate = Q(name__icontains=query)
        if query.isascii() and query.isdecimal() and len(query) <= 10 and int(query) <= 2147483647:
            predicate |= Q(product_id=int(query))
        products = products.filter(predicate)
    if cleaned.get("department"):
        products = products.filter(department_id=cleaned["department"])
    if cleaned.get("aisle"):
        products = products.filter(aisle_id=cleaned["aisle"])
    return products.order_by(*SORTS.get(cleaned.get("sort"), SORTS["orders"]))


def ranked_bars(records):
    records = list(records)
    maximum = max((row.occurrences for row in records), default=0)
    for row in records:
        row.bar_width = row.occurrences * 100 / maximum if maximum else 0
    return records


def product_dashboard():
    return {
        "snapshot": ProductImport.objects.filter(pk=1).first(),
        "top_products": list(Product.objects.select_related("department", "aisle").filter(order_count__gt=0).order_by("-order_count", "product_id")[:5]),
        "department_demand": ranked_bars(Department.objects.all()),
        "aisle_demand": ranked_bars(Aisle.objects.all()[:12]),
        "departments": list(Department.objects.order_by("name")),
        "aisles": list(Aisle.objects.order_by("name")),
    }
