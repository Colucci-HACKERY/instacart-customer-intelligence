# Product Intelligence development notes

## Source audit

Original CSV directory on this machine:
`C:\Users\akany\OneDrive - Botswana Accountancy College\Documents\ADA\ADA2026_Instacart_Project\data\raw\instacart`

The adjacent `data/interim` directory contains cleaned copies. Notebooks T01 and T05 read the original CSVs; T02/T03/T10 also use interim files. Existing Django `import_instacart` reads only `data/processed/t05_engineered_data.csv` and populates InstacartOrder. It does not import product relationships. That existing command clears order records before import; it was not run or changed for this module.

Verified original columns:

- products.csv: product_id, product_name, aisle_id, department_id
- aisles.csv: aisle_id, aisle
- departments.csv: department_id, department
- order_products__prior.csv and order_products__train.csv: order_id, product_id, add_to_cart_order, reordered
- orders.csv: order_id, user_id, eval_set, order_number, order_dow, order_hour_of_day, days_since_prior_order

The existing SQLite database contained only InstacartOrder and CustomerRFM analytical tables (plus Django system tables). Their model fields have not been removed or repurposed. Migration 0005 adds separate Product, Aisle, Department and ProductImport tables.

## Scope and definitions

Product Intelligence uses original prior-order rows only; train/test data is excluded. Its 3,214,874 distinct prior orders equal the existing order count numerically, but the order-ID sets differ. The T05 notebook merges prior basket sizes onto all orders, then drops rows with missing days_since_prior_order. Consequently the existing processed customer data excludes first orders and includes later train/test orders. A streamed comparison against original orders.csv confirmed that its population is not identical to the original prior-order set. New product summaries intentionally have no foreign key to the existing processed order rows; no existing records were repaired or recalculated. The published snapshot includes 49,688 catalog products, 21 departments, 134 aisles and 32,434,489 order-product occurrences.

- Containing orders: distinct source order IDs for each product/category.
- Occurrences: source order-product rows, not purchased quantities. Repeated product/order pairs count once for containing orders but every row contributes an occurrence.
- Reorder occurrences: rows with reordered exactly 1.
- Reorder rate (%): reorder occurrences / rows whose reordered is exactly 0 or 1 * 100. Missing/other indicators are excluded. A zero denominator is unknown, not zero.
- Category orders are deduplicated directly within each order, rather than summing product order counts. Category reorder rates use pooled row counts, not averages of product rates.
- No price, revenue, profit, dates, customer repurchase probability or inventory metrics are inferred.

Cards and charts describe the whole snapshot. Directory filters apply to the directory only. Product names and IDs are searchable; sort options rank containing orders, occurrences, reorders, reorder rate or alphabetical names. Pagination preserves query parameters. All catalog products remain discoverable, including zero-demand products.

## Import architecture and operation

The importer streams the large prior CSV. Memory holds the catalog and its counters plus sets for the current order, never the full order-product file. Canonical rows must be sorted by order_id; the importer checks this invariant. ID sorting is used solely to deduplicate grouped records, not as chronology. Unsorted files, broken dimension relationships, malformed rows, missing columns and empty sources fail before publication. Reorder indicator errors are counted as unknown rather than becoming fabricated zeroes.

A short database transaction publishes all product summaries together. A rerun replaces only derived product tables; any publication failure rolls back. Existing InstacartOrder and CustomerRFM rows are never written. ProductImport records the source filename, timestamp, counts and reorder coverage. Source CSVs remain unmodified outside this project.

From the Django project root in PowerShell:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py import_products --data-dir 'C:\Users\akany\OneDrive - Botswana Accountancy College\Documents\ADA\ADA2026_Instacart_Project\data\raw\instacart'
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test dashboard
.\.venv\Scripts\python.exe manage.py runserver
```

Migration and the complete original-data import have already been run. Reimport only when refreshing the source snapshot. The development server was restarted with Django auto-reload enabled.

## Query strategy and tests

Web requests read compact aggregates rather than joining or scanning 32 million raw rows. Product aisle/department names use select_related; product ranking fields and foreign keys are indexed. A populated page is covered by an eight-query regression check. Name substring search scans the approximately 50,000-row catalog; a normal name index cannot accelerate arbitrary contains searches. This is intentionally bounded to the catalog, not the fact file.

The dashboard test suite contains 28 tests: the original 15 customer/dashboard tests and 13 product tests. Product coverage includes duplicate-row semantics, distinct category orders, unknown/zero reorder values, sort and filter validation, pagination, escaping, bounded queries, source errors, idempotency, publication rollback and preservation of existing customer/order tables.

## Remaining limitations

Metrics are aggregate snapshots, refreshed by the management command. Raw order-product rows are not persisted in Django; per-customer product timelines and individual basket drilldowns are outside this module. CSVs must retain canonical order-ID grouping. The aisle chart ranks the top 12; every aisle is available in the product directory filter. High reorder rates can have small denominators; valid-row counts are shown per product. Existing dashboard chart libraries still load from their original external CDNs; new product charts are accessible HTML/CSS and require no chart CDN or JavaScript.

HTTP responses, static CSS delivery, URL/template resolution, Django checks, migration consistency and automated tests were verified. Browser visual/interaction testing could not be performed because no browser was connected to the session. In live checks product pages responded in approximately 0.04-0.37 seconds; the existing uncached Overview aggregation took approximately 14 seconds and remains a separate performance limitation.
