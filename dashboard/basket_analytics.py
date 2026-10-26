"""Basket snapshot queries and weighted summaries; no CSV reads on web requests."""
from decimal import Decimal, ROUND_CEILING

from django.db.models import Q

from .models import BasketPair, Product


SORTS = {"orders": "-order_count", "lift": "-lift", "confidence_a": "-confidence_a", "confidence_b": "-confidence_b"}


def basket_composition(histogram):
    rows = list(histogram)
    total = sum(row.order_count for row in rows)
    def value_at(position):
        cumulative = 0
        for row in rows:
            cumulative += row.order_count
            if cumulative >= position:
                return row.product_count
        return None
    median = (value_at((total + 1) // 2) + value_at((total + 2) // 2)) / 2 if total else None
    bins = []
    for lower, upper, label in ((1,5,"1-5 products"),(6,10,"6-10 products"),(11,15,"11-15 products"),(16,20,"16-20 products"),(21,30,"21-30 products"),(31,None,"31+ products")):
        selected = [row for row in rows if row.product_count >= lower and (upper is None or row.product_count <= upper)]
        orders = sum(row.order_count for row in selected)
        valid = sum(row.valid_reorder_count for row in selected)
        reorders = sum(row.reorder_count for row in selected)
        bins.append({"label":label, "orders":orders, "valid":valid, "reorders":reorders,
                     "share":orders*100/total if total else 0,
                     "reorder_rate":reorders*100/valid if valid else None})
    maximum = max((row["orders"] for row in bins), default=0)
    for row in bins:
        row["bar_width"] = row["orders"]*100/maximum if maximum else 0
    return {"median_products":median, "composition_bins":bins}


def pair_directory(cleaned, snapshot):
    pairs = BasketPair.objects.all()
    requested_count = int((Decimal(snapshot.order_count) * cleaned["min_support"] / 100).to_integral_value(rounding=ROUND_CEILING))
    pairs = pairs.filter(order_count__gte=max(snapshot.support_floor, cleaned["min_orders"], requested_count))
    confidence = cleaned.get("min_confidence") or 0
    pairs = pairs.filter(Q(confidence_a__gte=confidence) | Q(confidence_b__gte=confidence))
    for field in ("department", "aisle"):
        if cleaned.get(field):
            matching = Product.objects.filter(**{field + "_id":cleaned[field]}).order_by().values("product_id")
            pairs = pairs.filter(Q(product_a_id__in=matching) | Q(product_b_id__in=matching))
    query = cleaned.get("q", "")
    if query:
        predicate = Q(name__icontains=query)
        if query.isascii() and query.isdecimal() and len(query) <= 10 and int(query) <= 2147483647:
            predicate |= Q(product_id=int(query))
        matching = Product.objects.filter(predicate).order_by().values("product_id")
        pairs = pairs.filter(Q(product_a_id__in=matching) | Q(product_b_id__in=matching))
    return pairs.order_by(SORTS.get(cleaned.get("sort"), "-order_count"), "product_a_id", "product_b_id")


def label_pairs(page):
    records = list(page.object_list)
    identifiers = {pk for pair in records for pk in (pair.product_a_id, pair.product_b_id)}
    products = Product.objects.select_related("aisle", "department").in_bulk(identifiers)
    for pair in records:
        pair.product_a = products.get(pair.product_a_id)
        pair.product_b = products.get(pair.product_b_id)
    page.object_list = records
