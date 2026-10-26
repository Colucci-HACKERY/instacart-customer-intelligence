import csv
from contextlib import closing
import hashlib
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase
from django.urls import resolve, reverse

from . import views
from .models import (Aisle, BasketPair, BasketSize, BasketSnapshot, CustomerRFM,
                     Department, InstacartOrder, Product, ProductImport)
from .retention_analytics import ArtifactUnavailable, open_artifact
from .retention_build import (APPLICATION_ID, VERSION, artifact_path, build_retention,
                              histogram_median, recorded_gap, summarize_customer)


class RetentionIntelligenceTests(TransactionTestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/"orders.csv"
        self.output=self.root/"retention-v1.sqlite3"
        self.override=self.settings(RETENTION_ARTIFACT_PATH=self.output)
        self.override.enable()
        self.addCleanup(self.override.disable)
        CustomerRFM.objects.bulk_create([CustomerRFM(user_id=i,recency=100,frequency=3,monetary=9,
            recency_score=2,frequency_score=3,monetary_score=4,rfm_score=9,
            segment="Champions" if i%2 else "Loyal Customers") for i in list(range(1,30))+[31]])
        self.rows=[]
        for user in range(1,31):
            gaps={1:"",2:2,3:4,4:8}
            if user==2: gaps.update({2:30,3:7,4:1})
            if user==3: gaps.update({2:"",3:-1,4:0})
            if user==4: gaps.update({2:7,3:14,4:30})
            if user==6: gaps.update({2:5,3:6,4:""})
            # Both physical row order and numeric order IDs oppose the purchase sequence.
            for number in (3,1,4,2):
                sequence=2 if user==5 and number==3 else number
                self.rows.append([100000+user*100-number*10,user,"train" if number==4 else "prior",sequence,number%7,8,gaps[number]])
        self.write_source(self.rows)
        self.summary=build_retention(self.source,self.output)

    def write_source(self,rows,header=None):
        with self.source.open("w",newline="",encoding="utf-8") as target:
            writer=csv.writer(target)
            writer.writerow(header or ["order_id","user_id","eval_set","order_number","order_dow","order_hour_of_day","days_since_prior_order"])
            writer.writerows(rows)

    def read_customer(self,user):
        with closing(sqlite3.connect(self.output)) as store:
            store.row_factory=sqlite3.Row
            return dict(store.execute("SELECT * FROM cadence WHERE user_id=?",(user,)).fetchone())

    def get_page(self,**params):
        return self.client.get(reverse("retention_intelligence"),params)

    def test_sequence_uses_order_number_with_scrambled_ids_and_rows(self):
        record=self.read_customer(1)
        self.assertEqual(record["latest_number"],4)
        self.assertEqual(record["latest_gap"],8)
        self.assertEqual(record["earlier_mean"],3)
        self.assertEqual(record["earlier_n"],2)
        self.assertEqual(record["comparison"],"longer_recorded_gap")
        self.assertAlmostEqual(record["mean_gap"],14/3)
        self.assertEqual(record["median_gap"],4)

    def test_denominators_coverage_and_weighted_statistics(self):
        summary=self.summary
        self.assertEqual(summary["source_rows"],120)
        self.assertEqual(summary["source_customers"],30)
        self.assertEqual(summary["valid_sequence_customers"],29)
        self.assertEqual(summary["invalid_sequence_customers"],1)
        self.assertEqual(summary["excluded_sequence_rows"],4)
        self.assertEqual(summary["first_orders_excluded"],29)
        self.assertEqual(summary["eligible_gap_slots"],87)
        self.assertEqual(summary["valid_gaps"],84)
        self.assertEqual((summary["missing_gaps"],summary["invalid_gaps"]),(2,1))
        self.assertEqual((summary["within7"],summary["within14"]),(56,82))
        self.assertAlmostEqual(summary["share7"],100*56/84)
        self.assertAlmostEqual(summary["share14"],100*82/84)
        self.assertAlmostEqual(summary["mean_gap"],450/84)
        self.assertEqual(summary["median_gap"],4)
        self.assertEqual(summary["eval_sets"],{"prior":90,"train":30})
        self.assertEqual(len(summary["source_sha256"]),64)
        self.assertEqual(summary["format_version"],VERSION)

    def test_capping_prevents_directional_claims_and_latest_is_not_backfilled(self):
        self.assertEqual(self.read_customer(2)["comparison"],"capped_comparison")
        self.assertEqual(self.read_customer(4)["comparison"],"capped_comparison")
        self.assertEqual(self.read_customer(4)["latest_gap"],30)
        missing=self.read_customer(6)
        self.assertEqual(missing["comparison"],"latest_gap_unavailable")
        self.assertIsNone(missing["latest_gap"])
        self.assertEqual(missing["earlier_mean"],5.5)
        self.assertEqual(self.summary["capped_gaps"],2)

    def test_missing_invalid_and_same_day_gaps(self):
        record=self.read_customer(3)
        self.assertEqual((record["gap_n"],record["missing_gaps"],record["invalid_gaps"]),(1,1,1))
        self.assertEqual(record["latest_gap"],0)
        self.assertEqual(record["comparison"],"insufficient_history")
        for value in ("-1","31","NaN","Infinity","1.5","abc"):
            self.assertEqual(recorded_gap(value),(None,"invalid"))
        self.assertEqual(recorded_gap(""),(None,"missing"))
        self.assertEqual(recorded_gap("0.0"),(0,"valid"))

    def test_invalid_duplicate_missing_sequences_and_insufficient_history(self):
        def orders(numbers):
            return [{"order_id":100+i,"number":number,"raw_gap":"" if i==0 else "7","eval_set":"prior"} for i,number in enumerate(numbers)]
        for numbers in ([1,2,2],[1,3],[2,3],[1,None],[0,1]):
            record,counts=summarize_customer(1,orders(numbers))
            self.assertEqual(record["comparison"],"invalid_sequence")
            self.assertEqual(sum(counts),0)
        repeated=orders([1,2]); repeated[1]["order_id"]=repeated[0]["order_id"]
        self.assertEqual(summarize_customer(1,repeated)[0]["comparison"],"invalid_sequence")
        record,_=summarize_customer(1,orders([1]))
        self.assertEqual(record["comparison"],"insufficient_history")
        self.assertIsNone(record["mean_gap"])
        self.assertEqual(self.read_customer(5)["gap_n"],0)

    def test_directional_comparisons_are_descriptive(self):
        for latest,expected in ((1,"shorter_recorded_gap"),(3,"same_recorded_gap"),(8,"longer_recorded_gap")):
            orders=[{"order_id":100-i,"number":i,"raw_gap":str(gap),"eval_set":"prior"} for i,gap in enumerate(("",2,4,latest),start=1)]
            self.assertEqual(summarize_customer(1,orders)[0]["comparison"],expected)

    def test_first_order_gap_is_always_excluded_and_medians_handle_even_counts(self):
        rows=[{"order_id":2,"number":1,"raw_gap":"30","eval_set":"prior"}, {"order_id":1,"number":2,"raw_gap":"0","eval_set":"train"}]
        record,counts=summarize_customer(1,rows)
        self.assertEqual(record["unexpected_first_gap"],1)
        self.assertEqual(record["mean_gap"],0)
        self.assertEqual(sum(counts),1)
        self.assertEqual(histogram_median([1,0,1]),1)
        self.assertIsNone(histogram_median([0]*31))

    def test_search_segment_filters_and_live_rfm_labels(self):
        response=self.get_page(user_id="0001")
        self.assertEqual([r.user_id for r in response.context["cadence_page"]],[1])
        self.assertContains(response,"/customers/?user_id=1#profile")
        response=self.get_page(segment="Champions")
        self.assertEqual(response.context["cadence_page"].paginator.count,15)
        CustomerRFM.objects.filter(user_id=1).update(segment="Loyal Customers")
        response=self.get_page(user_id=1,segment="Loyal Customers")
        self.assertEqual(response.context["cadence_page"].paginator.count,1)
        self.assertEqual(response.context["cadence_page"][0].segment,"Loyal Customers")
        response=self.get_page(user_id=30)
        self.assertContains(response,"No RFM record")

    def test_source_rfm_population_differences_are_explicit(self):
        response=self.get_page()
        self.assertEqual(response.context["matched_customers"],29)
        self.assertEqual(response.context["source_without_rfm"],1)
        self.assertEqual(response.context["rfm_without_source"],1)
        self.assertContains(response,"1 source customers without an RFM record")
        self.assertContains(response,"prior 90, train 30 and test 0")

    def test_sorting_filters_and_pagination(self):
        response=self.get_page(page=2)
        self.assertEqual([r.user_id for r in response.context["cadence_page"]],list(range(26,31)))
        self.assertEqual(self.get_page(sort="latest").context["cadence_page"][0].user_id,4)
        self.assertEqual(self.get_page(sort="mean").context["cadence_page"][0].user_id,4)
        response=self.get_page(comparison="invalid_sequence")
        self.assertEqual([r.user_id for r in response.context["cadence_page"]],[5])
        response=self.get_page(sort="orders")
        self.assertContains(response,"sort=orders")
        self.assertContains(response,"page=2")
        for value in ("abc","0","-1","99999","9"*5000):
            self.assertEqual(self.get_page(page=value).status_code,200)

    def test_validation_empty_search_results_and_escaping(self):
        for params in ({"user_id":"-1"},{"user_id":"1.5"},{"user_id":"1 OR 1=1"},{"user_id":"2147483648"},{"user_id":"9"*5000},{"segment":"Unknown"},{"sort":"DROP TABLE"},{"comparison":"churned"}):
            with self.subTest(params=str(params)[:80]):
                response=self.get_page(**params)
                self.assertEqual(response.status_code,200)
                self.assertTrue(response.context["retention_form"].errors)
                self.assertEqual(response.context["cadence_page"].paginator.count,0)
        self.assertContains(self.get_page(user_id=999),"No customer cadence records match")
        self.assertNotContains(self.get_page(user_id="<script>"),"<script>")

    def test_missing_corrupt_and_unsupported_artifacts_are_friendly(self):
        for content in (None,b"not sqlite"):
            target=self.root/"unavailable.sqlite3"
            if content: target.write_bytes(content)
            with self.settings(RETENTION_ARTIFACT_PATH=target):
                response=self.get_page()
                self.assertEqual(response.status_code,200)
                self.assertContains(response,"Cadence data is not ready")
        with closing(sqlite3.connect(self.output)) as store: store.execute("PRAGMA user_version=999"); store.commit()
        self.assertContains(self.get_page(),"Unsupported cadence artifact version")
        with closing(sqlite3.connect(self.output)) as store:
            store.execute(f"PRAGMA user_version={VERSION}")
            store.execute("UPDATE metadata SET value=? WHERE key='summary'", (json.dumps({"format_version":VERSION}),))
            store.commit()
        self.assertContains(self.get_page(),"metadata is invalid")

    def test_no_valid_gaps_display_unknown_and_no_false_percentages(self):
        self.write_source([[10,1,"prior",1,1,8,""],[3,1,"train",2,1,8,""]])
        summary=build_retention(self.source,self.output)
        self.assertEqual(summary["valid_gaps"],0)
        self.assertIsNone(summary["share7"])
        self.assertIsNone(summary["mean_gap"])
        response=self.get_page()
        self.assertContains(response,"Unknown")
        self.assertEqual(response.context["snapshot"].missing_gaps,1)

    def test_bad_source_and_publish_failure_preserve_previous_artifact(self):
        before=self.output.read_bytes()
        for rows,header in (([],None),(list(reversed(self.rows)),None),([[1,"bad","prior",1,0,8,""]],None),([[1,1,"unknown",1,0,8,""]],None),([[1]], ["wrong"])):
            self.write_source(rows,header)
            with self.assertRaises(CommandError): build_retention(self.source,self.output)
            self.assertEqual(self.output.read_bytes(),before)
        self.write_source(self.rows)
        with patch("dashboard.retention_build.os.replace",side_effect=OSError("publish blocked")):
            with self.assertRaises(CommandError): build_retention(self.source,self.output)
        self.assertEqual(self.output.read_bytes(),before)
        self.assertEqual(list(self.root.glob(".retention-*")),[])

    def test_builder_refuses_application_database_source_and_unrelated_file(self):
        from django.conf import settings
        unrelated=self.root/"unrelated.txt"; unrelated.write_text("preserve")
        for target in (settings.BASE_DIR/"db.sqlite3",self.source,unrelated):
            with self.assertRaises(CommandError): build_retention(self.source,target)
        self.assertEqual(unrelated.read_text(),"preserve")

    def test_builder_and_get_preserve_all_existing_records(self):
        InstacartOrder.objects.create(order_id=55,user_id=1,order_dow=2,order_size=5)
        Department.objects.create(pk=1,name="produce")
        Aisle.objects.create(pk=1,name="fruit")
        Product.objects.create(pk=1,name="Apple",aisle_id=1,department_id=1,order_count=10)
        models=(InstacartOrder,CustomerRFM,Product,Aisle,Department,ProductImport,BasketPair,BasketSize,BasketSnapshot)
        before=[list(model.objects.order_by("pk").values()) for model in models]
        with self.assertNumQueries(0):
            call_command("build_retention",orders_csv=str(self.source),output=str(self.output),verbosity=0)
        self.get_page(segment="Champions",page=2)
        after=[list(model.objects.order_by("pk").values()) for model in models]
        self.assertEqual(before,after)

    def test_reader_is_query_only_and_paginates_in_sql(self):
        statements=[]
        original=sqlite3.connect
        def traced(*args,**kwargs):
            result=original(*args,**kwargs)
            result.set_trace_callback(statements.append)
            return result
        with patch("dashboard.retention_analytics.sqlite3.connect",side_effect=traced):
            response=self.get_page(page=2)
        self.assertEqual(len(response.context["cadence_page"]),5)
        self.assertTrue(any("LIMIT 5 OFFSET 25" in sql for sql in statements))
        self.assertLessEqual(sum(sql.lstrip().upper().startswith("SELECT") for sql in statements),10)
        with self.assertRaises(ArtifactUnavailable):
            with open_artifact() as store:
                store.execute("UPDATE platform.customer_rfm SET rfm_score=0")
        self.assertEqual(CustomerRFM.objects.filter(rfm_score=9).count(),30)

    def test_route_template_and_analytical_warnings(self):
        self.assertEqual(resolve(reverse("retention_intelligence")).func,views.retention_intelligence)
        response=self.get_page()
        self.assertTemplateUsed(response,"dashboard/retention.html")
        for text in ("30+ days","A latest completed gap ends with a subsequent purchase", "not 7-day or 14-day retention rates", "order_number", 'aria-current="page"', "Customer Retention"):
            self.assertContains(response,text)
