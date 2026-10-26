from django.db.models import Avg, Count, Sum

from .models import InstacartOrder


def get_customer_order_summary(orders):
    """Summarize only the supplied customer's actual orders; retain missing values."""
    return orders.aggregate(
        total_orders=Count("id"),
        known_baskets=Count("order_size"),
        total_products=Sum("order_size"),
        average_basket=Avg("order_size"),
        known_intervals=Count("days_since_prior_order"),
        average_interval=Avg("days_since_prior_order"),
    )


def get_dataset_summary():
    """Return the main Instacart dashboard KPIs."""

    total_orders = InstacartOrder.objects.count()

    total_customers = (
        InstacartOrder.objects
        .values("user_id")
        .distinct()
        .count()
    )

    average_order_size = (
        InstacartOrder.objects
        .aggregate(avg=Avg("order_size"))["avg"]
    )

    average_days = (
        InstacartOrder.objects
        .aggregate(
            avg=Avg("days_since_prior_order")
        )["avg"]
    )

    # Calculate median using only non-null values.
    order_sizes = (
        InstacartOrder.objects
        .filter(order_size__isnull=False)
        .values_list("order_size", flat=True)
        .order_by("order_size")
    )

    count = order_sizes.count()

    if count == 0:
        median = 0
    elif count % 2 == 1:
        median = order_sizes[count // 2]
    else:
        lower = order_sizes[(count // 2) - 1]
        upper = order_sizes[count // 2]
        median = (lower + upper) / 2

    return {
        "customers": total_customers,
        "orders": total_orders,
        "average_order_size": round(
            float(average_order_size or 0), 2
        ),
        "median_order_size": round(
            float(median or 0), 2
        ),
        "average_days_between_orders": round(
            float(average_days or 0), 2
        ),
        "rows": total_orders,
    }


def get_orders_by_day():
    """Return order counts by day of week."""

    result = (
        InstacartOrder.objects
        .values("order_dow")
        .annotate(count=Count("id"))
        .order_by("order_dow")
    )

    day_names = {
        0: "Sunday",
        1: "Monday",
        2: "Tuesday",
        3: "Wednesday",
        4: "Thursday",
        5: "Friday",
        6: "Saturday",
    }

    return {
        day_names[row["order_dow"]]: row["count"]
        for row in result
    }


def get_order_size_distribution():
    """Return order counts grouped by basket size."""

    result = (
        InstacartOrder.objects
        .values("order_size")
        .annotate(count=Count("id"))
        .order_by("order_size")
    )

    return {
        str(int(row["order_size"])): row["count"]
        for row in result
        if row["order_size"] is not None
    }


def get_reorder_interval():
    """Return order counts grouped by reorder interval."""

    result = (
        InstacartOrder.objects
        .values("days_since_prior_order")
        .annotate(count=Count("id"))
        .order_by("days_since_prior_order")
    )

    return {
        str(int(row["days_since_prior_order"])): row["count"]
        for row in result
        if row["days_since_prior_order"] is not None
    }
