# Stage 6: Customer Retention Intelligence

## Architecture and approved scope

The existing Django dashboard application, SQLite application database, function views and shared navigation/theme remain intact. The new route is `/retention/` (URL name `retention_intelligence`). No model changes or database migrations are introduced. Neither calculate_rfm nor any existing import/recalculation command is run.

This page describes historical recorded purchasing cadence, not current inactivity, predicted churn, calendar retention or financial value. It reads original orders.csv, using order_number to establish each customer's sequence. Order IDs are identifiers only; neither row insertion order nor numeric order IDs establish chronology.

Original source path:
`C:\Users\akany\OneDrive - Botswana Accountancy College\Documents\ADA\ADA2026_Instacart_Project\data\raw\instacart\orders.csv`

Verified source fields are order_id, user_id, eval_set, order_number, order_dow, order_hour_of_day and days_since_prior_order. Day-of-week/hour fields cannot supply calendar dates. Existing imported orders omit sequence/hour/evaluation fields and first orders. Current RFM recency is an existing relative proxy, not a calendar recency measure; it is never used for the new cadence metrics. Stored RFM scores/segments remain untouched and are joined read-only as contextual labels.

## Metrics and denominators

A valid customer sequence contains unique order IDs and exactly one of each order_number from 1 through that customer's last supplied order. Original rows may be in any order within a customer block; the builder explicitly sorts by order_number. Customer blocks must be grouped in ascending user_id, as the canonical source is. Reappearing/out-of-order customer blocks abort publication rather than silently combining incomplete histories.

- First-order gaps are always excluded. Unexpected nonempty first-order gaps are counted as source-quality anomalies.
- Valid non-first-order gaps are finite whole-day values 0 through 30. Zero is same-day, not missing. Missing, negative, fractional, nonfinite and out-of-range values are excluded and counted separately.
- The source caps days_since_prior_order at 30. The chart therefore separates 30+ days. Mean and median summarize capped, recorded observations, not exact uncapped elapsed times. Their denominator is the count of valid completed gaps.
- Within-7/14-day shares count valid gaps <=7/14, including zero, divided by all valid recorded completed gaps. They are not customer retention rates and do not use customers or all potential gap slots as their denominator.
- A customer mean/median uses that customer's valid recorded gaps. The global mean/median uses the pooled exact histogram, never an unweighted average of customer averages.
- Latest means the gap attached to the highest supplied order_number. A missing latest gap is never replaced by an earlier observation.
- Earlier mean excludes both the first order and the latest order. Directional comparisons require at least two earlier valid gaps, a valid latest gap, and no capped values on either side. Missing earlier gaps are excluded; the earlier sample size is displayed. Comparisons are neutral descriptions (longer/shorter/same recorded gap), not risk scores.
- Customers with duplicate, missing, nonpositive or nonconsecutive sequence numbers, or duplicate order IDs within their history, remain visible as invalid-sequence records but their entire history is excluded from gap metrics. Singleton and insufficient histories remain visible without a comparison.

A latest completed gap ends with a subsequent purchase. Even a long completed gap is evidence of a past pause followed by purchase; it cannot establish ongoing inactivity. No shared calendar origin, transaction dates, observation cutoff or post-final-order follow-up exists. The selected sample also does not provide an unbiased business retention rate. No spending, revenue, profit, transaction dates or lifetime value are invented.

## Source populations and initial results

The source has 3,421,083 order-metadata rows across 206,209 customers: 3,214,874 prior, 131,209 train and 75,000 test. Final train/test metadata supplies completed preceding gaps even where basket contents are held out. Retention therefore includes all provided metadata. Product and Basket Intelligence use prior-order contents only; the processed customer dashboard drops first orders. These populations are explicitly distinguished on the page.

The complete build found 206,209 valid customer sequences, zero invalid sequences, 206,209 excluded first orders, and 3,214,874 valid completed gaps. There were no missing/invalid non-first gaps in the original source. All 206,209 source customers match existing RFM records.

- Recorded capped mean: 11.114836 days; median: 7 days.
- Within 7 days: 1,620,033 / 3,214,874 = 50.391804%.
- Within 14 days: 2,355,684 / 3,214,874 = 73.274536%.
- Capped observations: 369,323.
- Comparisons: 25,039 longer recorded gaps; 28,297 shorter; 861 same; 152,012 limited by a capped observation. These are not churn counts.

## Artifact and read path

Default cache: `artifacts/retention-v1.sqlite3`. This is separate from db.sqlite3 and is not a Django-managed database. Format v1 is recorded in the filename, SQLite application_id/user_version and metadata. Metadata includes source byte hash, size, modification time, coverage and build timestamp. The current artifact is approximately 30.7 MB.

The builder streams source rows, holds one customer history at a time (10,000-order safety ceiling), aggregates an exact 31-value histogram, and writes customer summaries in batches of 1,000. It does not load the full CSV into a DataFrame or modify source records. A temporary sibling file is closed before atomic replacement. Validation/publication failures preserve the previous artifact, and unrelated files, source CSVs and the application database cannot be output targets. The source's size/mtime are checked before and after reading; SHA-256 identifies the bytes read.

The request reader opens the artifact read-only, attaches the application database read-only, and enables query_only. It joins only current RFM labels/IDs; no score recalculation occurs. Current RFM changes are reflected without rebuilding cadence. Unknown source/RFM customer populations are counted explicitly. SQL handles filtering, counts, stable sorting and 25-customer pages; only a page of records is materialized. Artifact indexes support exact ID, recorded-gap, source-order-count and comparison queries. Missing, corrupt or unsupported artifacts produce a friendly empty state.

Cards/charts describe the full source snapshot; directory filters affect only the directory. Customer ID search is exact and validated. Segment filters use current existing RFM labels, and comparison filters remain descriptive. Detail links open the existing Customer Explorer.

Optional configuration: `RETENTION_ARTIFACT_PATH` can point to another v1 cache; the default needs no settings change.

## Commands (PowerShell, project root)

The full build and verification have already run. Rebuild only to refresh source-derived cadence:

```powershell
.\.venv\Scripts\python.exe manage.py build_retention --orders-csv 'C:\Users\akany\OneDrive - Botswana Accountancy College\Documents\ADA\ADA2026_Instacart_Project\data\raw\instacart\orders.csv'
.\.venv\Scripts\python.exe manage.py test dashboard
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py migrate --check
```

For a separate output, add `--output <artifact-path>` and configure RETENTION_ARTIFACT_PATH consistently. No migrate/apply, calculate_rfm or existing data reimport is needed.

## Tests and verification

There are 63 tests: the previous 45 and 18 retention tests. New cases cover scrambled IDs/physical row order, capped and missing gaps, exact pooled denominators, weighted medians, invalid/duplicate sequences, insufficient history, zero-day intervals, non-backfilled latest gaps, source-population differences, current RFM joins, sorting, filtering, pagination, validation/escaping, missing/corrupt artifacts, output protection, atomic failure recovery and preservation of existing records. Artifact reads are checked for query-only enforcement and SQL pagination.

Initial tests caught a Windows file-handle issue when refreshing an artifact. Validation connections now close explicitly before replacement; full-data refresh and regression tests pass. No unresolved test failures remain. Django checks, migration consistency and live HTTP checks cover all six pages and retention filter/error states. Browser visual/interaction testing was not performed.

Before/after preservation uses counts of all existing analytical tables, a SHA-256 digest of all ordered RFM record values, and a byte-level SHA-256 digest of db.sqlite3. All preservation checks passed. db.sqlite3 is byte-for-byte unchanged, with SHA-256 `d04bfb5a9a36645da13f5baa2f3a47f3ebbb7c9e24ac4adf452e3ecf2891c8f2` before and after. All ordered RFM record values hash to `7bf4513d4645149ef2bc0de926f524bcdc608b045a038fa764765df9d84dece1` before and after. Counts remain 3,214,874 orders, 206,209 RFM records, 49,688 products, 134 aisles, 21 departments, one ProductImport, 74 basket pairs, 113 basket-size rows and one BasketSnapshot. Final full suite: 63 tests passed. System checks passed; makemigrations --check --dry-run reports no changes; migrate --check passed. All six live pages returned HTTP 200. Mean/latest cadence sorting uses its artifact indexes without a temporary sorting B-tree.

## Limitations

This is a manually refreshed source snapshot. Dates, current inactivity, churn probabilities, calendar cohorts and uncapped long intervals cannot be recovered. Missing or quarantined data can affect representativeness. Directional comparisons are deliberately conservative when 30+ observations are present. The builder expects canonical customer grouping and fails safely for malformed structural input. A refresh attempted while Windows holds an artifact file open may fail safely; rerun when readers finish. The existing Overview aggregation remains unchanged and slower than the snapshot-backed pages.

## Change log

- dashboard/retention_build.py (new streaming builder and artifact format)
- dashboard/retention_analytics.py (new read-only artifact query layer)
- dashboard/management/commands/build_retention.py (new command)
- dashboard/forms.py (retention filters)
- dashboard/views.py (retention view)
- dashboard/urls.py (retention route)
- dashboard/templates/dashboard/retention.html (new page)
- dashboard/templates/dashboard/navbar.html (sixth module link)
- dashboard/static/dashboard/theme.css (scoped cadence styles)
- dashboard/test_retention.py (new tests)
- artifacts/retention-v1.sqlite3 (generated cache, outside application database)
- RETENTION_INTELLIGENCE.md (this document)

No models, existing analytics functions, migrations, RFM commands or application records are changed.
