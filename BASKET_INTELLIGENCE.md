# Basket & Reorder Intelligence

## Source and schema audit

The existing SQLite database holds InstacartOrder, CustomerRFM, Product, Aisle, Department and ProductImport analytical tables. Product's product_id is its primary key; aisle_id and department_id refer to the existing aisle and department tables. ProductImport describes a prior-order aggregate snapshot, not raw basket memberships. Existing product_import.py streams source rows and calculates aggregate demand, but raw order-product rows are not persisted in Django.

The original data directory is:
`C:\Users\akany\OneDrive - Botswana Accountancy College\Documents\ADA\ADA2026_Instacart_Project\data\raw\instacart`

Inspected original columns:

- order_products__prior.csv: order_id, product_id, add_to_cart_order, reordered
- products.csv: product_id, product_name, aisle_id, department_id
- aisles.csv: aisle_id, aisle
- departments.csv: department_id, department

The basket builder verifies the source catalog against existing names and category relationships before processing. It uses original prior-order memberships and excludes train/test records. The processed customer/order population differs from original prior orders; identical total order counts do not imply identical order-ID sets. No joins to processed customer-order records are used for basket denominators.

Migration 0006 adds BasketSnapshot, BasketSize and BasketPair. It neither alters nor clears any existing model's records. Pair members retain original scalar product IDs, then resolve names, aisles and departments through a batched query against the existing Product relationships. This avoids changing the working product importer's delete/recreate lifecycle: a product refresh cannot cascade-delete or block basket snapshots. If that importer is refreshed later, the page shows a catalog freshness notice. Missing current catalog labels retain the original product ID and show an explicit unavailable-label message.

## Metric definitions

Let N be all distinct nonempty source order IDs, n(A) the number of those orders containing A, and n(A,B) the number containing both A and B.

- Basket size: the number of distinct product IDs in each order. A repeated source row does not increase basket size.
- Products per order: mean distinct basket size, including all nonempty source orders. The median is calculated from the exact weighted size histogram before presentation bands are formed.
- Source occurrences: all order-product rows, including repeated rows. These are not quantities purchased.
- Reorder occurrences: source rows with reordered exactly 1.
- Reorder rate (%): reordered rows / rows with reordered exactly 0 or 1 * 100. Missing/other indicators are excluded. A zero valid denominator displays Unknown. Rates for size bands pool valid rows in the entire band; they are not averages of basket-level rates.
- Pair support (%): 100 * n(A,B) / N.
- Confidence A to B (%): 100 * n(A,B) / n(A). Reverse confidence divides by n(B).
- Lift: n(A,B) * N / (n(A) * n(B)). Lift is symmetric; values above/below 1 indicate more/less co-occurrence than independence would predict.

Each unordered pair is stored once with lower product ID designated A. Both confidence directions are displayed; this ID convention does not imply causality or a purchase sequence. Search and category filters do not change N or either marginal. Category filters can match either member; multiple filters can match different members of a pair. Minimum-confidence filtering accepts either direction, while confidence sorting explicitly chooses one direction.

Association is not causation. These are descriptive patterns, not intervention effects or proof of recommendation uplift. Customers can contribute multiple dependent baskets. Minimum support reduces rare-pair noise but is not a significance test or a multiple-comparison correction. No prices, revenue, profit or unavailable dates are inferred.

## Efficient exact processing

The builder makes two sequential streaming passes over order_products__prior.csv. It never loads a full DataFrame, builds a product-by-product matrix, samples orders or truncates baskets.

1. Group canonical rows by ascending order_id; validate IDs and sorted grouping. Keep only one membership set at a time. Count product marginals, all-basket totals, exact size frequencies and row-level reorder coverage.
2. Set support floor to max(min_orders, ceil(N * min_support_percent / 100)). Products whose marginal is below this floor cannot possibly form a qualifying pair and are pruned by the Apriori property.
3. Count canonical combinations of eligible distinct members. Sparse Python counters spill to a temporary SQLite file at 100,000 unique pending pairs. The temporary store uses a bounded 32 MiB page cache. Combinations are generated per basket, not as an all-product matrix. Publication reads qualifying pairs in batches of 500.
4. Compare SHA-256 byte hashes of both passes and recheck the catalog signature. Any source/catalog change rejects publication.
5. Atomically replace only the new basket-derived records. A failure rolls back. Existing customer, order, RFM, product, aisle, department and product-import records are never written.

Temporary files are cleaned on completion/failure. Original CSVs remain unchanged. Orders are grouped by ID solely to identify membership, not to establish chronology. Empty orders are unobservable in this source and excluded from N.

The web page reads indexed published summaries, never the raw CSV. Pair queries have count/lift indexes, stable tie ordering, SQL pagination and batched product/category labels. A populated page has an eight-query regression budget. Product-name/category searches use SQL subqueries against the approximately 50,000-product catalog.

## Initial build and controls

The complete initial build used 0.5% minimum support and 1,000 minimum co-orders. The effective floor is 16,075 baskets, across 3,214,874 source baskets. It published 74 frequent pairs. All qualifying pairs above that floor were counted exactly, including both products' marginals across the entire population.

Directory controls can raise the floor. They cannot retrieve lower-support pairs omitted at build time. An attempted lower effective floor shows a validation explanation instead of silently returning incomplete results. Rebuild with lower command thresholds when broader exploration is required; this uses more disk and computation. The page shows the current build floor and imported timestamp. Even a build with no qualifying pairs still publishes the full basket/reorder summaries.

## Commands (PowerShell, project root)

The migration and initial full-data build have already been applied.

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py build_baskets --data-dir 'C:\Users\akany\OneDrive - Botswana Accountancy College\Documents\ADA\ADA2026_Instacart_Project\data\raw\instacart' --min-support 0.5 --min-orders 1000
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py migrate --check
.\.venv\Scripts\python.exe manage.py test dashboard
```

The new route is /baskets/ with URL name basket_intelligence. Shared navigation keeps all four existing pages and adds Basket & Reorder. The page reuses theme.css and adds only scoped basket layout rules. Accessible HTML/CSS charts require no JavaScript or external chart library.

## Verification and limitations

The full suite contains 45 tests: 28 previous regression tests plus 17 basket tests. Synthetic data exercises exact support/confidence/lift, negative association, duplicate memberships, unknown reorder indicators, weighted medians, Apriori pruning, full-population denominators, disk-counter flushing, pagination, filters, invalid controls, bounded queries, catalog mismatch, source changes, idempotency, publication rollback and preservation of all existing model records. Tests also confirm the existing product importer can refresh without deleting basket snapshots.

Django checks, migration consistency, the complete test suite, live HTTP responses and indexed query plans were verified. Browser visual/interaction verification was not performed.

The module is a refreshable snapshot, not live recomputation on every request. Only pair associations are mined; larger itemsets, per-customer recommendations, time-based changes, causal effects and individual basket drilldowns are outside scope. Very low support thresholds or unusually large baskets can require substantial temporary disk space and processing time even though memory remains bounded. The builder requires canonical ascending order-ID grouping and a matching existing catalog. Temporary disk failures and source validation errors abort rather than publish partial results.

## Files changed in this implementation

- dashboard/models.py
- dashboard/forms.py
- dashboard/views.py
- dashboard/urls.py
- dashboard/basket_import.py (new)
- dashboard/basket_analytics.py (new)
- dashboard/management/commands/build_baskets.py (new)
- dashboard/migrations/0006_basketsize_basketsnapshot_basketpair.py (new)
- dashboard/templates/dashboard/baskets.html (new)
- dashboard/templates/dashboard/navbar.html
- dashboard/static/dashboard/theme.css
- dashboard/test_baskets.py (new)
- BASKET_INTELLIGENCE.md (new)
- db.sqlite3 (new basket tables, snapshot and migration metadata only)
