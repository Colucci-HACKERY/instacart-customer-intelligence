from django.urls import path
from . import views


urlpatterns = [
    path("retention/", views.retention_intelligence, name="retention_intelligence"),
    path("baskets/", views.basket_intelligence, name="basket_intelligence"),
    path("products/", views.product_intelligence, name="product_intelligence"),

    path(
        "",
        views.home,
        name="home"
    ),

    path(
        "rfm/",
        views.rfm_dashboard,
        name="rfm_dashboard"
    ),

    path(
        "customers/",
        views.customer_explorer,
        name="customer_explorer"
    ),

]