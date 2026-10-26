from django.db import models


class InstacartOrder(models.Model):

    order_id = models.IntegerField(unique=True)

    user_id = models.IntegerField()

    order_dow = models.IntegerField()

    days_since_prior_order = models.FloatField(
        null=True,
        blank=True
    )

    order_size = models.FloatField(
        null=True,
        blank=True
    )


    class Meta:
        db_table = "instacart_orders"
        indexes = [
            models.Index(
                fields=["user_id", "order_id"],
                name="orders_customer_order_idx",
            ),
            models.Index(
                fields=["order_size"],
                name="orders_basket_size_idx",
            ),
            models.Index(
                 fields=["days_since_prior_order"],
                 name="orders_reorder_interval_idx",
            ),
            models.Index(
                 fields=["order_dow"],
                 name="orders_day_of_week_idx",
            ),
        ]


    def __str__(self):
        return f"Order {self.order_id}"


class CustomerRFM(models.Model):

    user_id = models.IntegerField(
        unique=True
    )

    recency = models.FloatField()

    frequency = models.IntegerField()

    monetary = models.FloatField()

    recency_score = models.IntegerField()

    frequency_score = models.IntegerField()

    monetary_score = models.IntegerField()

    rfm_score = models.IntegerField()

    segment = models.CharField(
        max_length=50
    )

    class Meta:
        db_table = "customer_rfm"
        indexes = [
            models.Index(fields=["segment", "user_id"], name="rfm_segment_customer_idx"),
        ]

        ordering = [
            "-rfm_score"
        ]

    def __str__(self):
        return (
            f"Customer {self.user_id} - "
            f"{self.segment}"
        )


class DemandMetrics(models.Model):
    order_count = models.PositiveBigIntegerField(default=0)
    occurrences = models.PositiveBigIntegerField(default=0)
    reorder_count = models.PositiveBigIntegerField(default=0)
    valid_reorder_count = models.PositiveBigIntegerField(default=0)
    reorder_rate = models.FloatField(null=True, blank=True)

    class Meta:
        abstract = True


class Department(DemandMetrics):
    department_id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=200)

    class Meta:
        ordering = ["-occurrences", "department_id"]


class Aisle(DemandMetrics):
    aisle_id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=200)

    class Meta:
        ordering = ["-occurrences", "aisle_id"]


class Product(DemandMetrics):
    product_id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=500, db_index=True)
    aisle = models.ForeignKey(Aisle, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-order_count", "product_id"]
        indexes = [
            models.Index(fields=["-order_count", "product_id"], name="product_orders_idx"),
            models.Index(fields=["-occurrences", "product_id"], name="product_occurrences_idx"),
            models.Index(fields=["-reorder_rate", "product_id"], name="product_reorder_rate_idx"),
            models.Index(fields=["-reorder_count", "product_id"], name="product_reorders_idx"),
        ]


class ProductImport(models.Model):
    # A single published snapshot; raw order-product rows stay in the source CSV.
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    source_name = models.CharField(max_length=200)
    imported_at = models.DateTimeField(auto_now=True)
    order_count = models.PositiveBigIntegerField()
    occurrences = models.PositiveBigIntegerField()
    valid_reorder_count = models.PositiveBigIntegerField()
    reorder_count = models.PositiveBigIntegerField()
    product_count = models.PositiveIntegerField()

    @property
    def invalid_reorder_count(self):
        return self.occurrences - self.valid_reorder_count

    @property
    def reorder_rate(self):
        if self.valid_reorder_count:
            return self.reorder_count * 100 / self.valid_reorder_count
        return None


class BasketSnapshot(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    source_name = models.CharField(max_length=200)
    imported_at = models.DateTimeField(auto_now=True)
    catalog_signature = models.CharField(max_length=64)
    source_sha256 = models.CharField(max_length=64)
    order_count = models.PositiveBigIntegerField()
    occurrences = models.PositiveBigIntegerField()
    memberships = models.PositiveBigIntegerField()
    reorder_count = models.PositiveBigIntegerField()
    valid_reorder_count = models.PositiveBigIntegerField()
    product_count = models.PositiveIntegerField()
    pair_count = models.PositiveIntegerField()
    support_floor = models.PositiveBigIntegerField()
    requested_support_pct = models.FloatField()
    requested_min_orders = models.PositiveIntegerField()

    @property
    def reorder_rate(self):
        return self.reorder_count * 100 / self.valid_reorder_count if self.valid_reorder_count else None

    @property
    def average_products(self):
        return self.memberships / self.order_count if self.order_count else 0

    @property
    def support_floor_pct(self):
        return self.support_floor * 100 / self.order_count if self.order_count else 0

    @property
    def unknown_reorders(self):
        return self.occurrences - self.valid_reorder_count


class BasketSize(models.Model):
    product_count = models.PositiveIntegerField(primary_key=True)
    order_count = models.PositiveBigIntegerField()
    occurrences = models.PositiveBigIntegerField()
    valid_reorder_count = models.PositiveBigIntegerField()
    reorder_count = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["product_count"]

    @property
    def reorder_rate(self):
        return self.reorder_count * 100 / self.valid_reorder_count if self.valid_reorder_count else None


class BasketPair(models.Model):
    # Source identifiers rather than FK deletion dependencies: refreshing the
    # existing product importer must not delete or block this separate snapshot.
    product_a_id = models.PositiveIntegerField(db_index=True)
    product_b_id = models.PositiveIntegerField(db_index=True)
    order_count = models.PositiveBigIntegerField()
    a_order_count = models.PositiveBigIntegerField()
    b_order_count = models.PositiveBigIntegerField()
    support = models.FloatField()
    confidence_a = models.FloatField()
    confidence_b = models.FloatField()
    lift = models.FloatField()

    class Meta:
        ordering = ["-order_count", "product_a_id", "product_b_id"]
        constraints = [
            models.UniqueConstraint(fields=["product_a_id", "product_b_id"], name="basket_pair_unique"),
            models.CheckConstraint(condition=models.Q(product_a_id__lt=models.F("product_b_id")), name="basket_pair_canonical"),
        ]
        indexes = [
            models.Index(fields=["-order_count", "product_a_id", "product_b_id"], name="basket_pair_orders_idx"),
            models.Index(fields=["-lift", "product_a_id", "product_b_id"], name="basket_pair_lift_idx"),
        ]
