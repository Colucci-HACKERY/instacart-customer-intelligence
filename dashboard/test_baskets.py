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
from django.utils import timezone

from . import views
from .basket_import import build_basket_data
from .basket_analytics import basket_composition
from .models import (Aisle, BasketPair, BasketSize, BasketSnapshot, CustomerRFM,
                     Department, InstacartOrder, Product, ProductImport)
from .product_import import import_product_data


class BasketIntelligenceTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        Department.objects.bulk_create([Department(pk=1, name="produce"), Department(pk=2, name="bakery")])
        Aisle.objects.bulk_create([Aisle(pk=1, name="fruit"), Aisle(pk=2, name="bread")])
        names = ("Apple", "Banana", "Cherry", "Dough", "Egg bread", "No demand")
        Product.objects.bulk_create([Product(pk=i, name=name, aisle_id=1 if i<4 else 2, department_id=1 if i<4 else 2) for i,name in enumerate(names,start=1)])
        self.write("departments.csv", ["department_id","department"], [[1,"produce"],[2,"bakery"]])
        self.write("aisles.csv", ["aisle_id","aisle"], [[1,"fruit"],[2,"bread"]])
        self.write("products.csv", ["product_id","product_name","aisle_id","department_id"], [[i,name,1 if i<4 else 2,1 if i<4 else 2] for i,name in enumerate(names,start=1)])
        self.source_rows = [
            [1,1,1,1],[1,1,2,0],[1,2,3,1],
            [2,1,1,0],[2,2,2,""], [3,1,1,"bad"],[3,3,2,0],
            [4,1,1,1], [5,2,1,0],[5,3,2,1], [6,3,1,""],
            [7,4,1,1],[7,5,2,1], [8,4,1,0],[8,5,2,0], [9,4,1,""],
        ]
        self.write_orders(self.source_rows)
        self.snapshot = build_basket_data(self.root,"1",1,counter_limit=2)

    def write(self, filename, header, records):
        with (self.root / filename).open("w",newline="",encoding="utf-8") as target:
            writer=csv.writer(target)
            writer.writerow(header)
            writer.writerows(records)

    def write_orders(self, records):
        self.write("order_products__prior.csv", ["order_id","product_id","add_to_cart_order","reordered"], records)

    def get_page(self, **params):
        return self.client.get(reverse("basket_intelligence"),params)

    def test_known_pair_support_confidence_and_lift(self):
        ab=BasketPair.objects.get(product_a_id=1,product_b_id=2)
        self.assertEqual((ab.order_count,ab.a_order_count,ab.b_order_count),(2,4,3))
        self.assertAlmostEqual(ab.support,200/9)
        self.assertAlmostEqual(ab.confidence_a,50)
        self.assertAlmostEqual(ab.confidence_b,200/3)
        self.assertAlmostEqual(ab.lift,1.5)
        de=BasketPair.objects.get(product_a_id=4,product_b_id=5)
        self.assertAlmostEqual(de.lift,3)
        self.assertAlmostEqual(de.confidence_b,100)
        self.assertAlmostEqual(BasketPair.objects.get(product_a_id=1,product_b_id=3).lift,.75)
        self.assertEqual(BasketPair.objects.count(),4)
        self.assertFalse(BasketPair.objects.filter(product_a_id=6).exists())

    def test_duplicates_do_not_change_membership_but_do_count_as_rows(self):
        snapshot=self.snapshot
        self.assertEqual((snapshot.order_count,snapshot.memberships,snapshot.occurrences),(9,15,16))
        self.assertAlmostEqual(snapshot.average_products,15/9)
        self.assertEqual((snapshot.valid_reorder_count,snapshot.reorder_count,snapshot.unknown_reorders),(12,6,4))
        self.assertEqual(snapshot.reorder_rate,50)
        self.assertEqual(BasketSize.objects.get(pk=1).order_count,3)
        bucket=BasketSize.objects.get(pk=2)
        self.assertEqual((bucket.order_count,bucket.occurrences),(6,13))
        self.assertEqual(len(snapshot.source_sha256),64)

    def test_weighted_composition_and_median(self):
        summary=basket_composition(BasketSize.objects.all())
        self.assertEqual(summary["median_products"],2)
        self.assertEqual(summary["composition_bins"][0]["orders"],9)
        self.assertEqual(summary["composition_bins"][0]["share"],100)
        self.assertEqual(summary["composition_bins"][0]["reorder_rate"],50)
        self.assertIsNone(summary["composition_bins"][1]["reorder_rate"])
        # Even population: the median averages the two middle sizes.
        rows=[BasketSize(product_count=1,order_count=1,occurrences=1,valid_reorder_count=0,reorder_count=0), BasketSize(product_count=3,order_count=1,occurrences=3,valid_reorder_count=0,reorder_count=0)]
        self.assertEqual(basket_composition(rows)["median_products"],2)
        self.assertIsNone(basket_composition([])["median_products"])

    def test_count_floor_and_apriori_pruning_keep_full_population(self):
        snapshot=build_basket_data(self.root,"1",2,counter_limit=1)
        self.assertEqual(snapshot.support_floor,2)
        self.assertEqual(snapshot.order_count,9)
        self.assertEqual(BasketPair.objects.count(),2)
        self.assertAlmostEqual(BasketPair.objects.get(product_a_id=4).support,200/9)
        response=self.get_page(min_orders=1,min_support=1)
        self.assertContains(response,"rebuild the snapshot with a lower floor")
        self.assertEqual(response.context["pair_page"].paginator.count,0)
        response=self.get_page(min_orders=2,min_support=0)
        self.assertFalse(response.context["pair_form"].errors)
        self.assertEqual(response.context["pair_page"].paginator.count,2)

    def test_support_percentage_uses_ceiling_and_filters_keep_denominator(self):
        response=self.get_page(min_support=20,min_orders=1)
        self.assertEqual(response.context["pair_page"].paginator.count,2)
        response=self.get_page(min_support=25,min_orders=1)
        self.assertEqual(response.context["pair_page"].paginator.count,0)
        response=self.get_page(q="Apple")
        self.assertEqual(response.context["pair_page"].paginator.count,2)
        self.assertAlmostEqual(response.context["pair_page"][0].support,200/9)

    def test_filters_and_sorting(self):
        self.assertEqual(self.get_page(department=2).context["pair_page"].paginator.count,1)
        self.assertEqual(self.get_page(aisle=1).context["pair_page"].paginator.count,3)
        self.assertEqual(self.get_page(q="4").context["pair_page"].paginator.count,1)
        self.assertEqual(self.get_page(min_confidence=90).context["pair_page"].paginator.count,1)
        for sort in ("lift","confidence_a","confidence_b"):
            response=self.get_page(sort=sort)
            self.assertEqual(response.context["pair_page"][0].product_a_id,4)

    def test_invalid_inputs_and_page_numbers(self):
        for params in ({"min_support":"-1"},{"min_support":"nan"},{"min_orders":"0"},{"min_orders":"1.2"},{"min_confidence":"101"},{"sort":"sql"},{"department":"999"},{"aisle":"abc"},{"q":"x"*101}):
            with self.subTest(params=params):
                response=self.get_page(**params)
                self.assertEqual(response.status_code,200)
                self.assertTrue(response.context["pair_form"].errors)
                self.assertEqual(response.context["pair_page"].paginator.count,0)
        for value in ("abc","0","-1","999999","9"*5000):
            self.assertEqual(self.get_page(page=value).status_code,200)
        self.assertNotContains(self.get_page(q="<script>alert(1)</script>"),"<script>alert(1)</script>")

    def test_pagination_preserves_filters(self):
        # More synthetic rules to exercise the real 25-row page boundary.
        BasketPair.objects.bulk_create([BasketPair(product_a_id=100+i,product_b_id=200+i,order_count=1,a_order_count=1,b_order_count=1,support=100/9,confidence_a=100,confidence_b=100,lift=9) for i in range(30)])
        response=self.get_page(sort="lift",min_support=1,min_orders=1,page=2)
        self.assertEqual(len(response.context["pair_page"]),9)
        for value in ("sort=lift","min_support=1","min_orders=1","#pairs"):
            self.assertContains(response,value)
        self.assertContains(response,"Not in current catalog")

    def test_query_count_is_bounded(self):
        with self.assertNumQueries(8):
            response=self.get_page()
        self.assertEqual(response.status_code,200)
        with CaptureQueriesContext(connection) as queries:
            self.get_page(q="Apple",department=1)
        self.assertLessEqual(len(queries),8)

    def test_routes_template_definitions_and_navigation(self):
        self.assertEqual(resolve(reverse("basket_intelligence")).func,views.basket_intelligence)
        response=self.get_page()
        self.assertTemplateUsed(response,"dashboard/baskets.html")
        for text in ("See what belongs together", "Association is not causation", "Confidence A", "Confidence B", "not a statistical significance test", "No product-by-product matrix", 'aria-current="page"'):
            self.assertContains(response,text)

    def test_existing_tables_are_unchanged_and_rebuild_is_idempotent(self):
        InstacartOrder.objects.create(order_id=999,user_id=9,order_dow=1)
        CustomerRFM.objects.create(user_id=9,recency=2,frequency=1,monetary=3,recency_score=1,frequency_score=1,monetary_score=1,rfm_score=3,segment="Needs Attention")
        models=(InstacartOrder,CustomerRFM,Product,Aisle,Department,ProductImport)
        before=[list(model.objects.order_by("pk").values()) for model in models]
        call_command("build_baskets",data_dir=str(self.root),min_support="1",min_orders=1,stdout=StringIO())
        self.assertEqual(before,[list(model.objects.order_by("pk").values()) for model in models])
        self.assertEqual(BasketSnapshot.objects.count(),1)
        self.assertEqual(BasketPair.objects.count(),4)

    def test_existing_product_refresh_does_not_delete_or_block_basket_snapshot(self):
        import_product_data(self.root)
        self.assertEqual(BasketPair.objects.count(),4)
        self.assertContains(self.get_page(),"product catalog was refreshed")

    def test_empty_state_no_pairs_and_unknown_reorder_rate(self):
        snapshot=build_basket_data(self.root,"100",1)
        self.assertEqual(snapshot.pair_count,0)
        self.assertEqual(snapshot.order_count,9)
        self.assertContains(self.get_page(),"No pairs match these controls")
        self.write_orders([[1,1,1,"unknown"]])
        snapshot=build_basket_data(self.root,"1",1)
        self.assertIsNone(snapshot.reorder_rate)
        self.assertContains(self.get_page(),"Unknown")
        BasketPair.objects.all().delete()
        BasketSize.objects.all().delete()
        BasketSnapshot.objects.all().delete()
        self.assertContains(self.get_page(),"Ready to connect the basket")

    def test_bad_sources_preserve_published_basket_records(self):
        before=list(BasketPair.objects.order_by("pk").values())
        for records in (list(reversed(self.source_rows)),[[1,999,1,0]],[],[[1,1,1]],[["bad",1,1,1]]):
            self.write_orders(records)
            with self.assertRaises(CommandError):
                build_basket_data(self.root,"1",1)
            self.assertEqual(before,list(BasketPair.objects.order_by("pk").values()))
        self.write("order_products__prior.csv",["wrong"],[[1]])
        with self.assertRaises(CommandError):
            build_basket_data(self.root,"1",1)

    def test_catalog_mismatch_and_invalid_build_controls(self):
        Product.objects.filter(pk=1).update(name="Different")
        with self.assertRaises(CommandError):
            build_basket_data(self.root,"1",1)
        for pct,orders in (("0",1),("101",1),("NaN",1),("Infinity",1),("1",0)):
            with self.subTest(pct=pct,orders=orders),self.assertRaises(CommandError):
                build_basket_data(self.root,pct,orders)

    def test_source_change_between_passes_is_rejected(self):
        def progress(message):
            if message.startswith("Pair floor"):
                self.write_orders(self.source_rows+[[10,1,1,0]])
        with self.assertRaisesMessage(CommandError,"Source or product catalog changed"):
            build_basket_data(self.root,"1",1,progress)
        self.assertEqual(BasketSnapshot.objects.get(pk=1).order_count,9)
        self.assertEqual(BasketPair.objects.count(),4)

    def test_publication_failure_rolls_back(self):
        with patch.object(BasketPair.objects,"bulk_create",side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                build_basket_data(self.root,"1",1)
        self.assertEqual(BasketPair.objects.count(),4)
        self.assertEqual(BasketSize.objects.count(),2)
        self.assertEqual(BasketSnapshot.objects.get(pk=1).order_count,9)
