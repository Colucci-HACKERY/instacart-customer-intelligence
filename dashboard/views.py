from django.shortcuts import render
from django.db.models import Count, Avg
from django.core.paginator import Paginator
from .forms import CustomerSearchForm
from .models import CustomerRFM, InstacartOrder
import json
from django.core.cache import cache

from .analytics import (
    get_dataset_summary,
    get_orders_by_day,
    get_order_size_distribution,
    get_reorder_interval,
    get_customer_order_summary,
)


def home(request):
    cache_key = "instacart_overview_analytics_v1"
    analytics = cache.get(cache_key)

    if analytics is None:
        analytics = {
            "summary": get_dataset_summary(),
            "orders_by_day": get_orders_by_day(),
            "order_size_distribution": get_order_size_distribution(),
            "reorder_interval": get_reorder_interval(),
        }

        cache.set(cache_key, analytics, timeout=300)

    summary = analytics["summary"]

    context = {
        "title": "Instacart Customer Intelligence Platform",
        "customers": summary["customers"],
        "orders": summary["orders"],
        "average_order_size": summary["average_order_size"],
        "median_order_size": summary["median_order_size"],
        "average_days_between_orders": summary[
            "average_days_between_orders"
        ],
        "rows": summary["rows"],
        "orders_by_day": json.dumps(analytics["orders_by_day"]),
        "order_size_distribution": json.dumps(
            analytics["order_size_distribution"]
        ),
        "reorder_interval": json.dumps(
            analytics["reorder_interval"]
        ),
    }

    return render(request, "dashboard/home.html", context)

def rfm_dashboard(request):

    # ---------------------------------------------------------
    # 1. Total customers
    # ---------------------------------------------------------

    total_customers = CustomerRFM.objects.count()

    # ---------------------------------------------------------
    # 2. Segment distribution
    # ---------------------------------------------------------

    segment_data = list(
        CustomerRFM.objects
        .values("segment")
        .annotate(
            count=Count("id")
        )
        .order_by("-count")
    )

    # Add percentage to each segment
    for segment in segment_data:

        segment["percentage"] = round(
            (
                segment["count"]
                / total_customers
                * 100
            ),
            1
        ) if total_customers else 0

    # ---------------------------------------------------------
    # 3. Individual segment counts
    # ---------------------------------------------------------

    champions = CustomerRFM.objects.filter(
        segment="Champions"
    ).count()

    loyal_customers = CustomerRFM.objects.filter(
        segment="Loyal Customers"
    ).count()

    potential_loyalists = CustomerRFM.objects.filter(
        segment="Potential Loyalists"
    ).count()

    at_risk = CustomerRFM.objects.filter(
        segment="At Risk"
    ).count()

    needs_attention = CustomerRFM.objects.filter(
        segment="Needs Attention"
    ).count()

    # ---------------------------------------------------------
    # 4. Average RFM score
    # ---------------------------------------------------------

    average_rfm_score = (
        CustomerRFM.objects.aggregate(
            average=Avg("rfm_score")
        )["average"] or 0
    )

    # ---------------------------------------------------------
    # 5. RFM score distribution
    # ---------------------------------------------------------

    score_distribution = list(
        CustomerRFM.objects
        .values("rfm_score")
        .annotate(
            count=Count("id")
        )
        .order_by("rfm_score")
    )

    # ---------------------------------------------------------
    # 6. Top customers
    # ---------------------------------------------------------

    top_customers = (
        CustomerRFM.objects
        .all()
        .order_by("-rfm_score", "-monetary", "recency")[:20]
    )

    # ---------------------------------------------------------
    # 7. Context
    # ---------------------------------------------------------

    context = {

        "total_customers": total_customers,

        "champions": champions,

        "loyal_customers": loyal_customers,

        "potential_loyalists": potential_loyalists,

        "at_risk": at_risk,

        "needs_attention": needs_attention,

        "average_rfm_score": round(
            average_rfm_score,
            2
        ),

        "segment_data": segment_data,

        "score_distribution": score_distribution,

        "top_customers": top_customers,

    }

    return render(
        request,
        "dashboard/rfm.html",
        context
    )

def customer_explorer(request):
    customer = None
    order_page = None
    order_summary = None
    searched_id = request.GET.get("user_id", "").strip()
    selected_segment = request.GET.get("segment", "").strip()
    search_form = CustomerSearchForm(
        request.GET if "user_id" in request.GET else None
    )
    search_valid = search_form.is_bound and search_form.is_valid()
    if search_valid:
        # Profile lookup is independent of the directory's segment filter.
        customer = CustomerRFM.objects.filter(
            user_id=search_form.cleaned_data["user_id"]
        ).first()

    if customer:
        orders = InstacartOrder.objects.filter(user_id=customer.user_id)
        order_summary = get_customer_order_summary(orders)
        # No date or sequence field exists. ID sorting is stable, not chronological.
        order_paginator = Paginator(orders.order_by("order_id"), 20)
        order_paginator.count = order_summary["total_orders"]
        order_page = order_paginator.get_page(request.GET.get("orders_page"))
        day_names = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
        for order in order_page:
            order.day_label = (
                day_names[order.order_dow] if 0 <= order.order_dow <= 6 else "Unknown"
            )

    segments = list(
        CustomerRFM.objects.order_by("segment")
        .values_list("segment", flat=True).distinct()
    )
    customers = CustomerRFM.objects.all()
    if selected_segment:
        customers = customers.filter(
            segment=selected_segment
        )
    directory_page = Paginator(customers.order_by("user_id"), 25).get_page(
        request.GET.get("page")
    )
    context = {
        "customer": customer,
        "search_form": search_form,
        "customer_not_found": search_valid and customer is None,
        "searched_id": searched_id,
        "customers": directory_page,
        "directory_page": directory_page,
        "order_page": order_page,
        "order_summary": order_summary,
        "segments": segments,
        "selected_segment": selected_segment,
        "unknown_segment": bool(selected_segment and selected_segment not in segments),
    }
    return render(
        request,
        "dashboard/customer_explorer.html",
        context
    )



def product_intelligence(request):
    from .forms import ProductExplorerForm
    from .models import Product
    from .product_analytics import product_dashboard, product_directory

    form = ProductExplorerForm(request.GET)
    context = product_dashboard()
    if form.is_valid():
        for field, records in (("department", context["departments"]), ("aisle", context["aisles"])):
            value = form.cleaned_data.get(field)
            if value and value not in {record.pk for record in records}:
                form.add_error(field, "Choose an available category.")
    products = product_directory(form.cleaned_data) if not form.errors else Product.objects.none()
    context.update({
        "product_form": form,
        "product_page": Paginator(products, 25).get_page(request.GET.get("page")),
        "selected_department": request.GET.get("department", ""),
        "selected_aisle": request.GET.get("aisle", ""),
    })
    return render(request, "dashboard/products.html", context)



def basket_intelligence(request):
    from decimal import Decimal, ROUND_CEILING
    from .basket_analytics import basket_composition, label_pairs, pair_directory
    from .forms import BasketPairForm
    from .models import Aisle, BasketPair, BasketSize, BasketSnapshot, Department, ProductImport

    snapshot = BasketSnapshot.objects.filter(pk=1).first()
    departments = list(Department.objects.order_by("name"))
    aisles = list(Aisle.objects.order_by("name"))
    params = request.GET.copy()
    default_support = str(snapshot.requested_support_pct) if snapshot else "0.5"
    default_orders = snapshot.requested_min_orders if snapshot else 1000
    for key, value in (("min_support", default_support), ("min_orders", str(default_orders))):
        if key not in params:
            params[key] = value
    form = BasketPairForm(params)
    pairs = BasketPair.objects.none()
    if form.is_valid():
        for field, records in (("department", departments), ("aisle", aisles)):
            value = form.cleaned_data.get(field)
            if value and value not in {record.pk for record in records}:
                form.add_error(field, "Choose an available category.")
        if snapshot and not form.errors:
            count_floor = max(form.cleaned_data["min_orders"], int((Decimal(snapshot.order_count) * form.cleaned_data["min_support"] / 100).to_integral_value(rounding=ROUND_CEILING)))
            if count_floor < snapshot.support_floor:
                form.add_error(None, f"This snapshot contains pairs observed in at least {snapshot.support_floor:,} orders. Raise minimum support/co-orders to that floor, or rebuild the snapshot with a lower floor.")
            else:
                pairs = pair_directory(form.cleaned_data, snapshot)
    page = Paginator(pairs, 25).get_page(request.GET.get("page"))
    label_pairs(page)
    product_snapshot = ProductImport.objects.filter(pk=1).first()
    context = {
        "snapshot":snapshot, "pair_form":form, "pair_page":page,
        "departments":departments, "aisles":aisles,
        "selected_department":request.GET.get("department", ""),
        "selected_aisle":request.GET.get("aisle", ""),
        "catalog_refreshed": bool(snapshot and product_snapshot and product_snapshot.imported_at > snapshot.imported_at),
    }
    context.update(basket_composition(BasketSize.objects.all()) if snapshot else {})
    return render(request, "dashboard/baskets.html", context)



def retention_intelligence(request):
    from .forms import RetentionFilterForm
    from .retention_analytics import (ArtifactUnavailable, CadenceRecords, coverage,
                                      distribution, load_summary, open_artifact)

    segments=list(CustomerRFM.objects.order_by("segment").values_list("segment",flat=True).distinct())
    form=RetentionFilterForm(request.GET)
    valid=form.is_valid()
    if valid and form.cleaned_data.get("segment") and form.cleaned_data["segment"] not in segments:
        form.add_error("segment","Choose an available RFM segment.")
    context={"retention_form":form,"segments":segments,"selected_segment":request.GET.get("segment",""),
             "snapshot":None,"artifact_error":None,"cadence_page":Paginator([],25).get_page(1)}
    try:
        with open_artifact() as store:
            summary=load_summary(store)
            page=Paginator(CadenceRecords(store,form.cleaned_data) if not form.errors else [],25).get_page(request.GET.get("page"))
            # Materialize only the current slice before closing the artifact.
            page.object_list=list(page.object_list)
            context.update(snapshot=summary,cadence_page=page,gap_buckets=distribution(store,summary.valid_gaps),**coverage(store))
    except ArtifactUnavailable as exc:
        context["artifact_error"]=str(exc)
    return render(request,"dashboard/retention.html",context)
