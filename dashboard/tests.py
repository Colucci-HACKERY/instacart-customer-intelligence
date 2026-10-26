from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.db import connection
from django.template.loader import get_template
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from . import views
from .forms import CustomerSearchForm
from .models import CustomerRFM, InstacartOrder


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs).get("href", ""))


class CustomerExplorerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        CustomerRFM.objects.bulk_create([
            CustomerRFM(
                user_id=user_id, recency=20, frequency=23 if user_id == 1 else 0,
                monetary=9 if user_id == 1 else 0, recency_score=3,
                frequency_score=4, monetary_score=5, rfm_score=12,
                segment="Loyal Customers" if user_id == 1 else "Needs Attention",
            )
            for user_id in range(1, 32)
        ])
        # Reverse IDs on insertion to ensure neither insertion order nor row ID
        # is accidentally presented as a customer timeline.
        InstacartOrder.objects.bulk_create([
            InstacartOrder(
                user_id=1, order_id=100 + index, order_dow=index % 7,
                order_size=0 if index == 0 else (9 if index == 1 else None),
                days_since_prior_order=0 if index == 0 else (4 if index == 1 else None),
            )
            for index in reversed(range(23))
        ])
        InstacartOrder.objects.create(
            user_id=31, order_id=999, order_dow=6,
            order_size=None, days_since_prior_order=None,
        )

    def get_explorer(self, **params):
        return self.client.get(reverse("customer_explorer"), params)

    def test_directory_is_paginated_and_ordered(self):
        response = self.get_explorer()
        self.assertEqual(response.status_code, 200)
        page = response.context["directory_page"]
        self.assertEqual(page.paginator.count, 31)
        self.assertEqual([row.user_id for row in page], list(range(1, 26)))
        self.assertContains(response, "Choose a customer to explore")
        self.assertIsNone(response.context["order_page"])

    def test_validated_search_and_missing_customer(self):
        response = self.get_explorer(user_id=" 0001 ")
        self.assertEqual(response.context["customer"].user_id, 1)
        response = self.get_explorer(user_id="99999")
        self.assertTrue(response.context["customer_not_found"])
        self.assertContains(response, "No RFM profile found")

    def test_invalid_ids_have_validation_errors_and_no_order_queries(self):
        for value in ("", "0", "-1", "abc", "1.0", "1e2", "1 OR 1=1", "2147483648", "9" * 5000):
            with self.subTest(value=value[:30]), CaptureQueriesContext(connection) as queries:
                response = self.get_explorer(user_id=value)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["search_form"].errors)
                self.assertFalse(response.context["customer_not_found"])
                self.assertIsNone(response.context["customer"])
            self.assertFalse(any('"instacart_orders"' in query["sql"] for query in queries))

    def test_integer_limits(self):
        self.assertTrue(CustomerSearchForm({"user_id": "2147483647"}).is_valid())
        self.assertFalse(CustomerSearchForm({"user_id": "2147483648"}).is_valid())

    def test_profile_displays_stored_scores_and_segment(self):
        response = self.get_explorer(user_id=1)
        for text in ("Customer #1", "12/15", "3/5", "4/5", "5/5", "badge loyal-customers", "Monetary / purchase volume", "not chronology"):
            self.assertContains(response, text)
        self.assertNotContains(response, "$9")

    def test_history_uses_only_customer_orders_with_bounded_queries(self):
        with self.assertNumQueries(6):
            response = self.get_explorer(user_id=1)
        self.assertEqual([o.order_id for o in response.context["order_page"]], list(range(100, 120)))
        self.assertNotContains(response, "#999")
        summary = response.context["order_summary"]
        self.assertEqual(summary["total_orders"], 23)
        self.assertEqual(summary["total_products"], 9)
        self.assertEqual(summary["known_baskets"], 2)
        self.assertEqual(summary["average_basket"], 4.5)
        self.assertEqual(summary["known_intervals"], 2)
        self.assertEqual(summary["average_interval"], 2)
        self.assertContains(response, "Not recorded")
        self.assertContains(response, "<td>0</td>", html=True)

    def test_order_pagination(self):
        response = self.get_explorer(user_id=1, orders_page=2)
        self.assertEqual([o.order_id for o in response.context["order_page"]], [120, 121, 122])
        self.assertEqual(response.context["order_summary"]["total_orders"], 23)

    def test_segment_filter_does_not_hide_selected_profile(self):
        response = self.get_explorer(user_id=1, segment="Needs Attention", page=2)
        self.assertEqual(response.context["customer"].user_id, 1)
        page = response.context["directory_page"]
        self.assertEqual(page.paginator.count, 30)
        self.assertEqual([row.user_id for row in page], [27, 28, 29, 30, 31])

    def test_unknown_segment_and_empty_database(self):
        response = self.get_explorer(segment="Unknown <script>")
        self.assertTrue(response.context["unknown_segment"])
        self.assertContains(response, "No customers match this filter")
        self.assertNotContains(response, "<script>")
        CustomerRFM.objects.all().delete()
        response = self.get_explorer()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No customers match this filter")

    def test_profile_without_orders(self):
        response = self.get_explorer(user_id=2)
        self.assertContains(response, "No order records are available")
        self.assertIsNone(response.context["order_summary"]["average_basket"])
        self.assertIsNone(response.context["order_summary"]["total_products"])
        self.assertContains(response, "Unknown")

    def test_null_only_orders_and_invalid_day(self):
        InstacartOrder.objects.filter(user_id=31).update(order_dow=8)
        response = self.get_explorer(user_id=31)
        summary = response.context["order_summary"]
        self.assertEqual(summary["total_orders"], 1)
        self.assertEqual(summary["known_baskets"], 0)
        self.assertIsNone(summary["total_products"])
        self.assertIsNone(summary["average_interval"])
        self.assertContains(response, "Unknown")
        self.assertContains(response, "stored RFM frequency differs")

    def test_invalid_page_numbers_are_safe(self):
        for value in ("abc", "0", "-1", "999999", "9" * 5000):
            with self.subTest(value=value[:30]):
                response = self.get_explorer(user_id=1, page=value, orders_page=value)
                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(len(response.context["directory_page"]), 25)
                self.assertLessEqual(len(response.context["order_page"]), 20)

    def test_links_preserve_filters_and_reset_order_page_on_customer_change(self):
        response = self.get_explorer(user_id=1, segment="Needs Attention", orders_page=2)
        parser = LinkParser()
        parser.feed(response.content.decode())
        parsed_links = [(urlsplit(link), parse_qs(urlsplit(link).query)) for link in parser.links]
        directory_next = next(params for url, params in parsed_links if url.fragment == "directory" and params.get("page") == ["2"])
        self.assertEqual(directory_next["user_id"], ["1"])
        self.assertEqual(directory_next["orders_page"], ["2"])
        profile_link = next(params for url, params in parsed_links if url.fragment == "profile")
        self.assertNotIn("orders_page", profile_link)
        self.assertEqual(profile_link["segment"], ["Needs Attention"])
        order_link = next(params for url, params in parsed_links if url.fragment == "orders")
        self.assertEqual(order_link["segment"], ["Needs Attention"])
        self.assertEqual(order_link["user_id"], ["1"])

    def test_existing_routes_and_templates(self):
        for name, view, template in (
            ("home", views.home, "home.html"),
            ("rfm_dashboard", views.rfm_dashboard, "rfm.html"),
            ("customer_explorer", views.customer_explorer, "customer_explorer.html"),
        ):
            with self.subTest(name=name):
                self.assertEqual(resolve(reverse(name)).func, view)
                get_template("dashboard/" + template)
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "dashboard/" + template)

    def test_get_requests_do_not_modify_customer_data(self):
        before = list(CustomerRFM.objects.order_by("user_id").values())
        order_count = InstacartOrder.objects.count()
        self.get_explorer(user_id=1, page=2, orders_page=2)
        self.assertEqual(before, list(CustomerRFM.objects.order_by("user_id").values()))
        self.assertEqual(order_count, InstacartOrder.objects.count())
