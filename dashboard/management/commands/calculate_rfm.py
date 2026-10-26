from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from dashboard.models import InstacartOrder, CustomerRFM
import bisect


class Command(BaseCommand):

    help = "Calculate customer RFM scores from Instacart order history"

    def handle(self, *args, **options):

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "=============================================="
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "       INSTACART RFM CALCULATION"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "=============================================="
            )
        )
        self.stdout.write("")

        # ---------------------------------------------------------
        # 1. Aggregate customer-level purchasing behaviour
        # ---------------------------------------------------------

        self.stdout.write(
            "Step 1: Aggregating customer order history..."
        )

        customers = list(
            InstacartOrder.objects
            .values("user_id")
            .annotate(
                frequency=Count("order_id"),
                monetary=Sum("order_size"),
            )
        )

        total_customers = len(customers)

        self.stdout.write(
            f"Customers found: {total_customers:,}"
        )

        # ---------------------------------------------------------
        # 2. Calculate customer recency
        #
        # The processed Instacart dataset does not contain a
        # calendar order date.
        #
        # Therefore, we reconstruct each customer's relative
        # order timeline using days_since_prior_order.
        #
        # We then use the customer's final observed position
        # relative to the maximum observation period.
        # ---------------------------------------------------------

        self.stdout.write(
            "Step 2: Calculating relative customer recency..."
        )

        # Get all orders needed for recency calculation.
        #
        # order_id is NOT treated as a date.
        orders = (
            InstacartOrder.objects
            .values(
                "user_id",
                "order_id",
                "days_since_prior_order",
            )
            .order_by("user_id", "order_id")
        )

        # ---------------------------------------------------------
        # Build the cumulative order timeline for every customer.
        # ---------------------------------------------------------

        customer_last_position = {}

        current_user = None
        cumulative_days = 0.0

        for order in orders:

            user_id = order["user_id"]

            if user_id != current_user:

                current_user = user_id
                cumulative_days = 0.0

            else:

                days = order["days_since_prior_order"]

                if days is not None:
                    cumulative_days += float(days)

            customer_last_position[user_id] = cumulative_days

        # ---------------------------------------------------------
        # Determine the end of the observation period.
        #
        # Customers whose last order occurs later in the
        # reconstructed timeline are considered more recent.
        # ---------------------------------------------------------

        observation_end = max(
            customer_last_position.values()
        )

        # ---------------------------------------------------------
        # Recency:
        #
        # smaller number = more recent
        #
        # Example:
        #
        # observation_end = 100
        # customer last position = 90
        #
        # recency = 10 days
        # ---------------------------------------------------------

        customer_recency = {}

        for user_id, last_position in customer_last_position.items():

            customer_recency[user_id] = (
                observation_end - last_position
            )

        self.stdout.write(
            f"Observation period: {observation_end:.2f} days"
        )

        # ---------------------------------------------------------
        # 3. Remove previous RFM records
        # ---------------------------------------------------------

        self.stdout.write(
            "Step 3: Removing previous RFM records..."
        )

        CustomerRFM.objects.all().delete()

        # ---------------------------------------------------------
        # 4. Create customer RFM records
        # ---------------------------------------------------------

        self.stdout.write(
            "Step 4: Creating customer RFM records..."
        )

        records = []

        for customer in customers:

            user_id = customer["user_id"]

            records.append(
                CustomerRFM(
                    user_id=user_id,

                    recency=float(
                        customer_recency.get(
                            user_id,
                            observation_end
                        )
                    ),

                    frequency=int(
                        customer["frequency"] or 0
                    ),

                    monetary=float(
                        customer["monetary"] or 0
                    ),

                    recency_score=0,
                    frequency_score=0,
                    monetary_score=0,
                    rfm_score=0,

                    segment="Unclassified",
                )
            )

            if len(records) >= 5000:

                CustomerRFM.objects.bulk_create(
                    records,
                    batch_size=5000
                )

                records = []

        if records:

            CustomerRFM.objects.bulk_create(
                records,
                batch_size=5000
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Customer RFM records created successfully."
            )
        )

        # ---------------------------------------------------------
        # 5. Load RFM records
        # ---------------------------------------------------------

        self.stdout.write(
            "Step 5: Calculating RFM scores..."
        )

        records = list(
            CustomerRFM.objects.all()
        )

        if not records:

            self.stdout.write(
                self.style.ERROR(
                    "No customer records found."
                )
            )

            return

        total_records = len(records)

        # ---------------------------------------------------------
        # 6. Create sorted distributions
        # ---------------------------------------------------------

        recency_sorted = sorted(
            r.recency for r in records
        )

        frequency_sorted = sorted(
            r.frequency for r in records
        )

        monetary_sorted = sorted(
            r.monetary for r in records
        )

        # ---------------------------------------------------------
        # 7. Percentile scoring function
        # ---------------------------------------------------------

        def score(
            value,
            sorted_values,
            reverse=False
        ):

            position = bisect.bisect_right(
                sorted_values,
                value
            )

            percentile = (
                position / total_records
            )

            result = int(
                percentile * 5
            )

            result = max(
                1,
                min(
                    5,
                    result
                )
            )

            # Recency is reversed:
            #
            # lower number of days = better.
            #
            if reverse:

                result = 6 - result

            return result

        # ---------------------------------------------------------
        # 8. Calculate individual RFM scores
        # ---------------------------------------------------------

        for index, customer in enumerate(
            records,
            start=1
        ):

            # RECENCY
            #
            # Lower number of days is better.
            #
            customer.recency_score = score(
                customer.recency,
                recency_sorted,
                reverse=True
            )

            # FREQUENCY
            #
            # More orders is better.
            #
            customer.frequency_score = score(
                customer.frequency,
                frequency_sorted
            )

            # MONETARY / PURCHASE VOLUME
            #
            # More products purchased is better.
            #
            customer.monetary_score = score(
                customer.monetary,
                monetary_sorted
            )

            # -----------------------------------------------------
            # Combined RFM score
            # -----------------------------------------------------

            customer.rfm_score = (
                customer.recency_score
                + customer.frequency_score
                + customer.monetary_score
            )

            # -----------------------------------------------------
            # Customer segmentation
            # -----------------------------------------------------

            if customer.rfm_score >= 13:

                customer.segment = "Champions"

            elif customer.rfm_score >= 11:

                customer.segment = "Loyal Customers"

            elif customer.rfm_score >= 9:

                customer.segment = "Potential Loyalists"

            elif customer.rfm_score >= 7:

                customer.segment = "At Risk"

            else:

                customer.segment = "Needs Attention"

            if index % 25000 == 0:

                self.stdout.write(
                    f"Scored "
                    f"{index:,}/"
                    f"{total_records:,} customers..."
                )

        # ---------------------------------------------------------
        # 9. Save calculated scores
        # ---------------------------------------------------------

        self.stdout.write(
            "Step 6: Saving RFM scores..."
        )

        CustomerRFM.objects.bulk_update(
            records,
            [
                "recency_score",
                "frequency_score",
                "monetary_score",
                "rfm_score",
                "segment",
            ],
            batch_size=5000
        )

        # ---------------------------------------------------------
        # 10. Verification
        # ---------------------------------------------------------

        zero_scores = CustomerRFM.objects.filter(
            rfm_score=0
        ).count()

        champions = CustomerRFM.objects.filter(
            segment="Champions"
        ).count()

        loyal = CustomerRFM.objects.filter(
            segment="Loyal Customers"
        ).count()

        potential = CustomerRFM.objects.filter(
            segment="Potential Loyalists"
        ).count()

        at_risk = CustomerRFM.objects.filter(
            segment="At Risk"
        ).count()

        attention = CustomerRFM.objects.filter(
            segment="Needs Attention"
        ).count()

        # ---------------------------------------------------------
        # Final output
        # ---------------------------------------------------------

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "=============================================="
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "       RFM CALCULATION COMPLETE"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                "=============================================="
            )
        )

        self.stdout.write(
            f"Total customers:       {total_records:,}"
        )

        self.stdout.write(
            f"Champions:             {champions:,}"
        )

        self.stdout.write(
            f"Loyal Customers:      {loyal:,}"
        )

        self.stdout.write(
            f"Potential Loyalists:  {potential:,}"
        )

        self.stdout.write(
            f"At Risk:               {at_risk:,}"
        )

        self.stdout.write(
            f"Needs Attention:      {attention:,}"
        )

        self.stdout.write(
            f"Zero RFM scores:      {zero_scores:,}"
        )

        self.stdout.write("")

        if zero_scores == 0:

            self.stdout.write(
                self.style.SUCCESS(
                    "✓ Verification passed: "
                    "No customers have an RFM score of zero."
                )
            )

        else:

            self.stdout.write(
                self.style.WARNING(
                    f"WARNING: {zero_scores:,} "
                    "customers have an RFM score of zero."
                )
            )

        self.stdout.write("")