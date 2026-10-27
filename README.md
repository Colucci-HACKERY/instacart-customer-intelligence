# Instacart Customer Intelligence Platform

A Django-based business intelligence platform for exploring customer purchasing behaviour, product demand, basket associations, and historical reorder cadence using the Instacart Market Basket Analysis dataset.

The project combines analytical reporting with data-processing pipelines, database-backed dashboards, automated tests, and an independently audited predictive-modelling workflow.

**Live Demo:** [View the live application](https://syd223.pythonanywhere.com/)

A data analytics and customer intelligence platform built with Python and Django to explore customer purchasing behaviour, RFM segmentation, product performance, basket patterns, and historical retention trends using large-scale Instacart transaction data.

**Tech Stack:** Python · Django · Pandas · SQLite · HTML · CSS · JavaScript


## Project objectives

- Transform large retail datasets into useful business intelligence.
- Explore basket composition, product demand, and product co-occurrence.
- Describe historical purchasing cadence without misrepresenting it as current churn or retention.
- Build interactive, searchable analytical pages using Django.
- Apply data validation, efficient querying, bounded-memory processing, and automated testing.
- Evaluate a customer repurchase prediction workflow separately from the live dashboard.

## Analytical modules

### 1. Overview and customer intelligence

The main dashboard presents high-level customer and order statistics, including order counts, basket-size summaries, ordering-day distributions, and recorded reorder intervals.

The processed customer-order table contains approximately 3.21 million records and 206,209 customers. Its population is not identical to the original prior-order population, so results must be interpreted according to the source used by each module.

### 2. Product Intelligence

Product Intelligence analyses the original prior-order product records and catalog.

The published snapshot covers:

- 49,688 catalog products
- 21 departments
- 134 aisles
- 32,434,489 order-product row occurrences

Features include product and category summaries, reorder metrics, searchable product records, sorting, filtering, and pagination.

The importer streams source rows and publishes derived summaries rather than persisting every raw order-product row in Django. Reorder rates exclude missing or invalid reorder indicators from their denominators.

### 3. Basket & Reorder Intelligence

This module explores which products occur together in customer baskets.

It calculates:

- Distinct products per basket
- Basket-size distributions
- Product co-occurrence
- Pair support
- Directional confidence
- Lift

The initial build used a 0.5% minimum-support threshold and a minimum of 1,000 co-orders, with an effective support floor of 16,075 baskets. It published 74 qualifying frequent pairs across 3,214,874 source baskets.

The builder uses sequential streaming passes, sparse counters, temporary SQLite storage, and batched publication. It avoids constructing a full product-by-product matrix.

**Interpretation:** co-occurrence and lift describe associations, not causation. They do not establish that recommendations increase sales or that one product causes another to be purchased.

### 4. Customer Retention Intelligence

This module describes historical recorded purchasing cadence using the original order metadata.

The initial snapshot contains:

- 206,209 customer sequences
- 3,214,874 valid completed reorder gaps
- Mean recorded gap: approximately 11.11 days
- Median recorded gap: 7 days
- Completed gaps within 7 days: approximately 50.39%
- Completed gaps within 14 days: approximately 73.27%

The source caps recorded gaps at 30 days. The module therefore reports capped historical intervals rather than exact uncapped elapsed times.

**Important limitation:** these measures do not establish current inactivity, calendar-based retention, or churn probability. A completed gap ends with a subsequent purchase, and the source does not provide the observation cutoff needed to determine whether a customer is currently inactive.

The module uses a separate versioned SQLite artifact and read-only query paths for its published snapshot.

### 5. Predictive-modelling audit

The predictive-modelling work was developed and evaluated separately from the live Django dashboard.

The local development environment contains audit evidence for feasibility, model evaluation, and independent verification. These files are excluded from the public repository because they may contain generated data, model artifacts, or other non-source outputs.

The Stage 7.3 work independently reconstructed customer labels and 60 features, checked customer-disjoint partitions, and evaluated model reproducibility and performance.

These results should be interpreted alongside the corresponding audit reports. The predictive model is not an integrated live prediction service, and the dashboard does not currently serve customer-level predictions from it.
## Technology stack

- **Language:** Python
- **Web framework:** Django
- **Application database:** SQLite
- **Data processing:** pandas and streaming Python processing, depending on the module
- **Analytics:** SQL aggregations, descriptive statistics, and frequent-pair metrics
- **Testing:** Django automated tests
- **Data storage:** CSV source files and versioned analytical SQLite artifacts
- **Interface:** Django templates, HTML, CSS, and the project's existing charting components

## Architecture

The application separates source processing from page rendering:

1. Original CSV datasets provide the source records.
2. Management commands validate and process source data.
3. Derived summaries are published to application tables or versioned analytical artifacts.
4. Django views retrieve summaries, apply validated filters, and paginate results.
5. Templates render the analytical pages.

The product and basket modules do not require raw order-product rows to be loaded into the web request path. Retention analytics are served from a separate artifact with read-only access.

## Running the application locally

A fresh clone contains the application source code and documentation, but not the populated local databases or generated analytical artifacts. Cloning the repository and starting Django will not, by itself, reproduce the full analytical dashboards.

### Prerequisites

- Python compatible with the versions specified in `requirements.txt`
- The project's required Python packages
- Original Instacart source files, obtained separately, if rebuilding the analytics

### Create a local environment

From the cloned project directory, open PowerShell and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Apply Django migrations and check the configuration:

```powershell
python manage.py migrate
python manage.py check
python manage.py makemigrations --check --dry-run
```

These commands initialise Django's database schema; they do not populate the analytical datasets.

### Data and analytical snapshots

The populated development environment uses a main SQLite database and a separate retention SQLite artifact. Both are excluded from the public source repository. The original Instacart datasets are also not included.

To reproduce the full dashboard, obtain the source data separately and follow the relevant module documentation to prepare the required inputs and build the analytical summaries.

**Important:** the existing `import_instacart` command uses a machine-specific CSV path and deletes existing order records before importing. Do not run it without first reviewing and configuring its input and protecting existing data.

The product and basket builders require the original catalog files and `order_products__prior.csv`. The retention builder requires `orders.csv` and produces a separate artifact. See:

- `PRODUCT_INTELLIGENCE.md`
- `BASKET_INTELLIGENCE.md`
- `RETENTION_INTELLIGENCE.md`

Until the required data and snapshots are available, a fresh installation should not be expected to display the same populated analytical results as the development environment.

### Run checks and start Django

```powershell
python manage.py test dashboard
python manage.py runserver
```

Open `http://127.0.0.1:8000/` in your browser.

The Django development server is intended for local development, not public production hosting.
## Testing and verification

The latest recorded dashboard regression run completed successfully:

- 63 tests discovered
- 63 tests passed
- No test failures
- Django system check passed
- `makemigrations --check` reported no changes detected

The analytical modules include tests for metric definitions, filtering, pagination, data validation, query bounds, publication failures, and preservation of existing records.

The project also contains audit-specific test and integrity evidence. These results should be read alongside their corresponding reports rather than treated as proof that every possible production scenario has been tested.

## Performance and data integrity

The project uses database indexes for commonly queried order fields and caches expensive Overview analytics in Django's local-memory cache.

The Overview cache has a five-minute timeout and is invalidated by the existing order-import command. Local-memory caching is suitable for the current single-process development setup; a multi-worker deployment would require a shared cache strategy and appropriate invalidation.

Other engineering measures documented by the analytical modules include:

- Streaming large CSV files instead of loading entire fact files into memory
- Batched database writes
- Indexed summary queries
- SQL-based filtering and pagination
- Source validation before publication
- Atomic replacement or rollback for derived snapshots
- Preservation checks for existing application records

Performance measurements depend on the environment and cache state. Browser load time is not the same as server-side analytics execution time.

## Repository guide

| Path | Purpose |
|---|---|
| `config/` | Django project configuration |
| `dashboard/` | Application views, models, analytics, forms, templates, and management commands |
| `BASKET_INTELLIGENCE.md` | Basket metrics, processing design, and limitations |
| `PRODUCT_INTELLIGENCE.md` | Product metrics, import architecture, and limitations |
| `RETENTION_INTELLIGENCE.md` | Cadence definitions, source populations, and limitations |
| `requirements.txt` | Python package dependencies |
| `Dockerfile` | Container build and startup configuration |
| `.dockerignore` | Files excluded from Docker build context |
| `.gitignore` | Files excluded from Git |
| `LICENSE` | MIT licence for the original project code |
| `manage.py` | Django management entry point |

The local development environment also contains SQLite databases, analytical artifacts, and audit outputs. These are excluded from the public source repository and are not supplied automatically by cloning it.



## Limitations

- The original datasets and local development environment are not bundled as a portable installation.
- Analytical summaries are snapshots and must be refreshed deliberately when source data changes.
- Source populations differ across the modules; their counts should not be combined without considering their definitions.
- Product co-occurrence is descriptive and does not prove causation or recommendation uplift.
- Recorded reorder gaps cannot establish current inactivity or churn.
- Predictive-modelling audit artifacts are not an integrated live prediction service.
- A successful automated test suite does not replace security review, deployment testing, or complete browser-based user acceptance testing.

## Further documentation

Consult the published module-specific documentation for metric definitions, source validation, rebuild procedures, test coverage, and known limitations.

- `BASKET_INTELLIGENCE.md`
- `PRODUCT_INTELLIGENCE.md`
- `RETENTION_INTELLIGENCE.md`

Local audit reports and generated artifacts are not included in the public source repository.


## Project status

The Django application passes its current system checks and 63-test regression suite. The project includes multiple analytical modules, source-processing workflows, performance improvements, and independent modelling audit evidence.

The next development priorities are reproducible environment setup, browser-based verification, security and deployment readiness, and a clear demonstration of the business value and limitations of the analyses.
