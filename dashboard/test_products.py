import csv
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from .models import Aisle, CustomerRFM, Department, InstacartOrder, Product, ProductImport
from .product_import import import_product_data
from . import views


class ProductIntelligenceTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write("departments.csv", ["department_id", "department"], [[1, "produce"], [2, "bakery"]])
        self.write("aisles.csv", ["aisle_id", "aisle"], [[1, "fruit"], [2, "bread"]])
        self.write("products.csv", ["product_id", "product_name", "aisle_id", "department_id"],
                   [[i, "Apple" if i == 1 else ("Pear" if i == 2 else f"Product {i:02d}"), 1 if i < 3 else 2, 1 if i < 3 else 2] for i in range(1, 31)])
        self.source_rows = [[10, 1, 1, 1], [10, 1, 2, 0], [10, 2, 3, 1], [11, 1, 1, "invalid"], [12, 2, 1, ""], [13, 3, 1, 0]]
        self.write_orders(self.source_rows)
        self.snapshot = import_product_data(self.root)

    def write(self, name, header, rows):
        with (self.root / name).open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(header)
            writer.writerows(rows)

    def write_orders(self, rows):
        self.write("order_products__prior.csv", ["order_id", "product_id", "add_to_cart_order", "reordered"], rows)

    def get_page(self, **params):
        return self.client.get(reverse("product_intelligence"), params)

    def test_distinct_orders_occurrences_and_reorder_denominator(self):
        apple = Product.objects.get(pk=1)
        self.assertEqual((apple.order_count, apple.occurrences), (2, 3))
        self.assertEqual((apple.reorder_count, apple.valid_reorder_count, apple.reorder_rate), (1, 2, 50))
        pear = Product.objects.get(pk=2)
        self.assertEqual((pear.order_count, pear.occurrences, pear.reorder_rate), (2, 2, 100))
        self.assertEqual(Product.objects.get(pk=3).reorder_rate, 0)
        self.assertIsNone(Product.objects.get(pk=4).reorder_rate)
        self.assertEqual((self.snapshot.order_count, self.snapshot.occurrences), (4, 6))
        self.assertEqual((self.snapshot.valid_reorder_count, self.snapshot.reorder_rate), (4, 50))

    def test_category_orders_are_deduplicated_not_summed(self):
        for category in (Department.objects.get(pk=1), Aisle.objects.get(pk=1)):
            self.assertEqual(category.order_count, 3)
            self.assertEqual(category.occurrences, 5)
            self.assertEqual(category.valid_reorder_count, 3)
            self.assertAlmostEqual(category.reorder_rate, 200 / 3)

    def test_directory_search_filters_and_empty_results(self):
        self.assertEqual(self.get_page(q="apple").context["product_page"].paginator.count, 1)
        self.assertEqual(self.get_page(q="1").context["product_page"][0].pk, 1)
        response = self.get_page(department=1, aisle=1)
        self.assertEqual(response.context["product_page"].paginator.count, 2)
        response = self.get_page(department=1, aisle=2)
        self.assertContains(response, "No products match your search")
        self.assertEqual(response.context["snapshot"].occurrences, 6)

    def test_sorting_and_null_rates_last(self):
        expected = {"orders": [1, 2, 3], "occurrences": [1, 2, 3], "reorders": [1, 2, 3], "reorder_rate": [2, 1, 3], "name": [1, 2, 3]}
        for sort, ids in expected.items():
            with self.subTest(sort=sort):
                response = self.get_page(sort=sort)
                self.assertEqual([p.pk for p in list(response.context["product_page"])[:3]], ids)

    def test_pagination_preserves_query_parameters(self):
        response = self.get_page(department=2, q="Product", sort="name", page=2)
        self.assertEqual(len(response.context["product_page"]), 3)
        self.assertContains(response, "department=2")
        self.assertContains(response, "q=Product")
        self.assertContains(response, "sort=name")
        for value in ("abc", "-1", "99999", "9" * 5000):
            with self.subTest(value=value[:20]):
                self.assertEqual(self.get_page(page=value).status_code, 200)

    def test_invalid_filters_and_escaped_search(self):
        for params in ({"department": "abc"}, {"aisle": "-1"}, {"department": "999"}, {"sort": "DROP TABLE"}, {"q": "x" * 101}):
            with self.subTest(params=params):
                response = self.get_page(**params)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["product_form"].errors)
                self.assertEqual(response.context["product_page"].paginator.count, 0)
        self.assertNotContains(self.get_page(q='<script>alert(1)</script>'), '<script>alert(1)</script>')

    def test_query_count_is_bounded_and_relationships_are_joined(self):
        with self.assertNumQueries(8):
            response = self.get_page()
        self.assertEqual(len(response.context["product_page"]), 25)
        with CaptureQueriesContext(connection) as queries:
            self.get_page(department=2, page=2)
        self.assertLessEqual(len(queries), 8)

    def test_url_navigation_and_definitions(self):
        self.assertEqual(resolve(reverse("product_intelligence")).func, views.product_intelligence)
        response = self.get_page()
        self.assertTemplateUsed(response, "dashboard/products.html")
        for text in ("Know what fills the basket", "Reorders / valid rows", "not quantities purchased", "Train/test data is excluded", "different order population", 'aria-current="page"', "dashboard/theme.css"):
            self.assertContains(response, text)

    def test_reimport_is_idempotent_and_preserves_existing_tables(self):
        InstacartOrder.objects.create(order_id=123, user_id=55, order_dow=1, order_size=4)
        CustomerRFM.objects.create(user_id=55, recency=10, frequency=1, monetary=4, recency_score=2, frequency_score=2, monetary_score=2, rfm_score=6, segment="Needs Attention")
        before = list(InstacartOrder.objects.values()), list(CustomerRFM.objects.values())
        call_command("import_products", data_dir=str(self.root), stdout=StringIO())
        self.assertEqual(Product.objects.count(), 30)
        self.assertEqual(ProductImport.objects.count(), 1)
        self.assertEqual(Product.objects.get(pk=1).occurrences, 3)
        self.assertEqual(before, (list(InstacartOrder.objects.values()), list(CustomerRFM.objects.values())))

    def test_bad_sources_preserve_the_published_snapshot(self):
        before = list(Product.objects.order_by("pk").values())
        for rows in (list(reversed(self.source_rows)), [[10, 999, 1, 1]], [], [[10, 1, 1]], [["bad", 1, 1, 1]]):
            with self.subTest(rows=rows):
                self.write_orders(rows)
                with self.assertRaises(CommandError):
                    import_product_data(self.root)
                self.assertEqual(before, list(Product.objects.order_by("pk").values()))
                self.assertEqual(ProductImport.objects.get(pk=1).occurrences, 6)

    def test_missing_columns_broken_relationships_and_duplicate_ids(self):
        self.write("products.csv", ["wrong"], [[1]])
        with self.assertRaises(CommandError):
            import_product_data(self.root)
        for rows in ([[1, "Apple", 999, 1]], [[1, "Apple", 1, 1], [1, "Duplicate", 1, 1]]):
            self.write("products.csv", ["product_id", "product_name", "aisle_id", "department_id"], rows)
            with self.assertRaises(CommandError):
                import_product_data(self.root)
        self.assertEqual(Product.objects.count(), 30)

    def test_publish_failure_rolls_back(self):
        with patch.object(Product.objects, "bulk_create", side_effect=RuntimeError("publication failed")):
            with self.assertRaises(RuntimeError):
                import_product_data(self.root)
        self.assertEqual(Product.objects.count(), 30)
        self.assertEqual(Aisle.objects.count(), 2)
        self.assertEqual(Department.objects.count(), 2)
        self.assertEqual(ProductImport.objects.get(pk=1).occurrences, 6)

    def test_empty_state_before_first_import(self):
        Product.objects.all().delete()
        Aisle.objects.all().delete()
        Department.objects.all().delete()
        ProductImport.objects.all().delete()
        response = self.get_page()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Product intelligence is ready for data")
