# Systematic Equity Data Pipeline

**Kolmogorov's team** | Version 2.2.0

---

## Overview

A production-grade ETL pipeline that ingests six years of financial market data for **678 publicly listed companies** across US, UK, European, Canadian, and Swiss exchanges. Data is stored in a triple-database architecture (PostgreSQL, MongoDB, MinIO) with event streaming to Apache Kafka.

The data infrastructure supports a **flow-based multi-factor equity strategy** grounded in Vayanos and Woolley (2012), targeting momentum, value, and quality signals arising from institutional fund flows.

---

## Data Sources

| # | Source | API | Records | Coverage |
|---|--------|-----|---------|----------|
| 1 | Daily prices (OHLCV + adjusted close) | Yahoo Finance | ~994k rows | 672 / 678 symbols |
| 2 | Quarterly / annual fundamentals | Yahoo Finance + SEC EDGAR | ~205k rows | 606 / 678 symbols |
| 3 | EDGAR supplementary fundamentals | SEC EDGAR XBRL | ~137k rows | 436 US symbols |
| 4 | Company financial ratios | Yahoo Finance + Finnhub | ~93k rows | 637 / 678 symbols |
| 5 | FX rates (GBP, EUR, CAD, CHF to USD) | Yahoo Finance | ~6.3k rows | 4 / 4 pairs |
| 6 | CBOE Volatility Index (VIX) | Yahoo Finance | ~1.5k rows | 2020-2026 |
| 7 | US 3-Month Treasury rate (DGS3MO) | FRED | ~1.6k rows | 2020-2026 |
| 8 | Regional benchmark indices (5) | Yahoo Finance | ~8k rows | S&P 500, FTSE 100, Euro Stoxx 50, TSX, SMI |
| 9 | ESG sustainability scores | LSEG / yfinance | 234 rows | ~35% (API ceiling) |
| 10 | News sentiment (VADER + financial boost) | yfinance + NewsAPI + GDELT | ~2k rows | 667 / 678 symbols |
| 11 | Computed ratios (B/P, E/P, CF/P, ROE, D/E, Earnings Stability) | Derived from sources 2 + 4 | per-ticker | 602 / 678 symbols |

**Date range:** 2020-02-27 to present (6-year backfill by default)

---

## Architecture

```
                          +---------------------------+
                          |      Main.py (ETL)        |
                          |   Parallel orchestration  |
                          +------------+--------------+
                                       |
          +----------------------------+----------------------------+
          |                            |                            |
   +------v------+            +--------v------+           +--------v------+
   | Yahoo Finance|            |  SEC EDGAR   |           |    FRED API  |
   |  yfinance   |            |  XBRL EDGAR  |           |  (DGS3MO)    |
   +------+------+            +--------+------+           +--------+------+
          |                            |                            |
          +----------------------------+----------------------------+
                                       |
                     +-----------------v-----------------+
                     |         Data Cleaning             |
                     |  Pydantic models + DQ checks      |
                     +-----------------+-----------------+
                                       |
          +----------------------------+----------------------------+
          |                            |                            |
   +------v------+            +--------v------+           +--------v------+
   |  PostgreSQL  |            |   MongoDB    |           |    MinIO     |
   |  (primary)   |            | (semi-struct)|           | (data lake)  |
   +------+------+            +--------------+           +--------------+
          |
   +------v------+
   |    Kafka    |
   |  (streaming)|
   +-------------+
```

**Orchestration groups (parallel execution):**

| Group | Sources | Notes |
|-------|---------|-------|
| A (parallel) | Prices + Fundamentals | Launched at t=0 |
| Independent (parallel) | FX + RFR + ESG + Sentiment | Launched at t=0, concurrent with Group A |
| A.5 + A.6 (parallel) | EDGAR (US) + Finnhub (non-US) | Start after Group A joins |
| B | VIX + Benchmark | Sequential (yfinance thread-safety) |
| C | Company Ratios | 3-source waterfall; 8 parallel workers |

**Pre-flight delisted detection:** After the prices phase, a multi-signal analysis identifies inactive tickers (stale prices, ingestion-log failures, ratio gaps) and confirms each via live yfinance verification. Confirmed inactive tickers are skipped in all subsequent phases.

---

## Prerequisites

| Dependency | Version | Purpose |
|------------|---------|---------|
| Python | 3.11+ | Runtime |
| PostgreSQL | 14+ | Primary data store (port 5438 in dev) |
| MongoDB | 6+ | Semi-structured document store |
| Apache Kafka | 3+ | Event streaming |
| MinIO | Latest | S3-compatible object store |
| Docker + Compose | 24+ | Infrastructure (all services containerised) |

---

## Quick Start

**1. Clone and install dependencies**

```bash
git clone https://github.com/abailey81/Big-Data-Pipeline.git
cd Big-Data-Pipeline
pip install poetry
poetry install
```

**2. Start infrastructure**

```bash
docker compose up --build -d
```

This starts PostgreSQL (5438), MongoDB (27017), MinIO (9000), and Kafka (9092).

**3. Configure environment**

```bash
cp .env.example .env.dev
# Edit .env.dev with your API keys:
#   FINNHUB_API_KEY  (free at finnhub.io)
#   NEWSAPI_KEY      (free at newsapi.org/register, optional)
```

**4. Run the full 6-year backfill**

```bash
poetry run python Main.py --env_type dev
```

**5. Run daily incremental update**

```bash
poetry run python Main.py --env_type dev --frequency daily
```

---

## CLI Reference

```
poetry run python Main.py --env_type <dev|docker> [OPTIONS]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--env_type` | required | `dev` (local) or `docker` |
| `--frequency` | `None` | `daily` / `weekly` / `monthly` / `quarterly`. Omit for full backfill. |
| `--start_date` | derived | Override start date (YYYY-MM-DD) |
| `--end_date` | today | Override end date (YYYY-MM-DD) |
| `--sources` | all | Space-separated subset: `prices fundamentals fx vix risk_free_rate benchmark ratios esg sentiment` |
| `--tickers` | all 678 | Override universe with specific tickers |
| `--init_schema` | false | Create/update PostgreSQL schema before running |
| `--dry_run` | false | Validate configuration without downloading |

---

## Project Structure

```
Big-Data-Pipeline/
├── Main.py                          # Pipeline entry point and orchestrator
├── config/
│   └── conf.yaml                    # Environment configuration
├── modules/
│   ├── input/                       # Downloaders (one per data source)
│   │   ├── price_downloader.py
│   │   ├── fundamentals_downloader.py
│   │   ├── edgar_downloader.py
│   │   ├── finnhub_downloader.py
│   │   ├── fx_downloader.py
│   │   ├── vix_downloader.py
│   │   ├── risk_free_rate_downloader.py
│   │   ├── esg_downloader.py
│   │   ├── news_downloader.py
│   │   ├── newsapi_downloader.py
│   │   └── gdelt_downloader.py
│   ├── processing/                  # Data cleaning and transformation
│   │   ├── data_cleaner.py
│   │   ├── data_quality.py
│   │   ├── sentiment_scorer.py      # VADER + financial domain boost
│   │   └── ticker_utils.py
│   ├── db_ops/                      # Database clients
│   │   ├── sql_conn.py              # PostgreSQL (SQLAlchemy + psycopg2)
│   │   ├── mongo_conn.py            # MongoDB (PyMongo)
│   │   ├── minio_store.py           # MinIO object store
│   │   └── kafka_ops.py             # Kafka producer
│   ├── data_models/
│   │   ├── models.py                # Pydantic validation models
│   │   └── table_models.py          # SQLAlchemy ORM definitions
│   └── utils/
│       ├── circuit_breaker.py       # Resilience pattern
│       ├── rate_limiter.py          # Token-bucket rate limiting
│       ├── retry.py                 # Exponential backoff decorator
│       ├── concurrent_executor.py   # ThreadPoolExecutor wrapper
│       ├── health_check.py          # Pre-flight dependency checks
│       ├── pipeline_metrics.py      # Timing and outcome tracking
│       ├── progress_tracker.py      # Rich terminal progress display
│       └── scheduler.py             # APScheduler cron integration
├── static/schema/
│   ├── create_tables.sql            # PostgreSQL DDL (12 tables)
│   └── company_static.csv           # Universe of 678 tickers
├── tests/                           # 877 tests, 92% coverage
├── docs/                            # Sphinx documentation
├── docker-compose.yml               # Infrastructure (8 services)
├── pyproject.toml
└── requirements.txt
```

---

## Database Schema

**PostgreSQL** (`systematic_equity` schema, 12 tables):

| Table | Primary Key | Description |
|-------|-------------|-------------|
| `company_static` | `symbol` | Universe of 678 companies |
| `daily_prices` | `(symbol, cob_date)` | OHLCV + adjusted close |
| `fundamentals` | `(symbol, report_date, field_name, period_type)` | EAV balance sheet / income statement |
| `fx_rates` | `(currency_pair, cob_date)` | GBP, EUR, CAD, CHF to USD |
| `vix_data` | `cob_date` | CBOE VIX daily |
| `risk_free_rate` | `cob_date` | US 3-Month T-Bill (DGS3MO) |
| `benchmark_index` | `(symbol, cob_date)` | 5 regional indices |
| `company_ratios` | `(symbol, snapshot_date, field_name)` | 33 financial ratios (EAV) |
| `esg_scores` | `(symbol, cob_date)` | Total ESG + component scores |
| `news_sentiment` | `(symbol, cob_date)` | VADER composite score + dispersion |
| `ingestion_log` | `log_id` | Audit trail for every download attempt |
| `pipeline_metadata` | `(data_source, symbol)` | Last successful run per source |

All tables use `ON CONFLICT DO UPDATE` for idempotent re-runs.

---

## Sentiment Scoring

**3-source news cascade** (per ticker, parallel across 6 workers):
1. **yfinance `Ticker.news`** — primary (no API key needed)
2. **NewsAPI `/v2/everything`** — secondary gap-fill (requires `NEWSAPI_KEY`)
3. **GDELT DOC API** — tertiary gap-fill (free, no key)

Each source triggers only when the previous returns zero articles.

**Composite score (0-100):**

```
sentiment_score = vader_component  * 0.45
               + positive_ratio    * 0.25
               + volume_component  * 0.20
               + agreement_bonus   * 0.10
```

---

## Resilience

| Mechanism | Purpose |
|-----------|---------|
| Circuit breaker | Stops retrying a broken API after N failures |
| Token-bucket rate limiter | Prevents rate limit breaches |
| Exponential backoff with jitter | Retries transient failures |
| Per-batch download timeout | Prevents stuck HTTP sockets from blocking |
| Thread-join hard caps | Prevents hung threads from blocking the pipeline |
| Kafka fire-and-forget | Kafka ACK latency never blocks DB writes |
| MongoDB socket timeout | Prevents indefinite socket hangs |
| Upsert-safe writes | Idempotent re-runs without duplicates |
| SIGINT / SIGTERM handler | Graceful shutdown between stages |

---

## Testing

### Testing Approach

The test suite follows a three-tier strategy aligned with the testing pyramid:

**Unit Tests** — Test individual modules in isolation with all external dependencies mocked (APIs, databases, network). Each module has a dedicated test file (e.g., `test_sql_conn.py`, `test_data_cleaning.py`, `test_circuit_breaker.py`). These run without any infrastructure and form the bulk of the suite.

**Integration Tests** — Test database upsert idempotency and schema initialisation against a live PostgreSQL instance. Located in `tests/test_integration.py`. These require Docker infrastructure and are automatically skipped when PostgreSQL is not available (via TCP socket probe), ensuring `poetry run pytest ./tests/` always passes cleanly.

**End-to-End Tests** — Test full pipeline workflows from CLI argument parsing through data cleaning to database writes (mocked at the boundary). Located in `tests/test_e2e.py`.

### Coverage Results

```
TOTAL                                         3109    242    92%
======================= 877 passed, 5 skipped =========================
```

**877 tests** across 30 test files. **Coverage: 92%** (well above the 80% minimum). The 5 skipped tests are integration tests that require a running PostgreSQL instance.

### Running Tests

```bash
# Full test suite (coverage included by default via pyproject.toml)
poetry run pytest ./tests/

# Unit tests only (no external dependencies needed)
poetry run pytest ./tests/ -m "not integration"

# With HTML coverage report
poetry run pytest ./tests/ --cov-report=html
```

---

## Code Quality

Three automated tools enforce consistent code quality across the project:

| Tool | Purpose | Configuration |
|------|---------|---------------|
| **Black** | Opinionated code formatter | `line-length = 110`, `target-version = ["py310"]` |
| **isort** | Import sorting (black-compatible) | `profile = "black"`, `line_length = 110` |
| **flake8** | PEP 8 linting and style checks | `.flake8` with per-file ignores |

All configuration is centralised in `pyproject.toml` (Black, isort) and `.flake8`.

```bash
# Check formatting compliance (no changes made)
poetry run black --check modules/ Main.py
poetry run isort --check-only modules/ Main.py

# Lint
poetry run flake8 modules/

# Auto-format
poetry run black modules/ tests/ Main.py
poetry run isort modules/ tests/ Main.py
```

**Current status:** All 44 source files pass Black, isort, and flake8 with zero violations.

---

## Security

Security scanning is performed using **Bandit** (static analysis) and **Safety** (dependency vulnerability scanning), both included as dev dependencies in `pyproject.toml`.

### Bandit Results (Static Analysis)

```bash
poetry run bandit -r modules/ -c pyproject.toml
```

| Severity | Count | Details |
|----------|-------|---------|
| **High** | **0** | No high-severity issues |
| **Medium** | 4 | `B310`: `urllib.urlopen` in EDGAR/Finnhub downloaders — intentional, URLs are hardcoded SEC/Finnhub API endpoints |
| **Low** | 6 | `B311`: `random.uniform` for jitter backoff — not cryptographic use; `B110`: `try/except/pass` in ESG fallback paths — intentional graceful degradation |

**Total lines scanned:** 7,232. **B101** (`assert`) is excluded via `pyproject.toml` as asserts are used only in tests.

All medium/low findings are intentional design decisions documented inline, not security vulnerabilities.

### Safety Results (Dependency Vulnerabilities)

```bash
poetry run safety check
```

**1 low-severity advisory** found in an indirect dependency — no production impact. All direct dependencies are pinned to current stable versions via Poetry's lock file.

---

## Pipeline Flexibility

The pipeline supports multiple run frequencies through the `--frequency` CLI argument, enabling both initial backfill and incremental updates:

| Frequency | Lookback Window | Use Case |
|-----------|----------------|----------|
| *(omitted)* | 6 years (full backfill) | Initial data seeding |
| `daily` | 5 business days | Nightly incremental update |
| `weekly` | 14 days | Weekly refresh with overlap buffer |
| `monthly` | 35 days | Month-end rebalance processing |
| `quarterly` | 95 days | Quarterly earnings window |

The lookback window is derived from the frequency flag and the `lookback_years` parameter in `config/conf.yaml`. Custom date ranges override frequency-based lookback:

```bash
# Daily incremental
poetry run python Main.py --env_type dev --frequency daily

# Custom range
poetry run python Main.py --env_type dev --start_date 2023-01-01 --end_date 2024-12-31

# Subset of sources
poetry run python Main.py --env_type dev --frequency daily --sources prices fundamentals fx

# Scheduled recurring execution (APScheduler)
poetry run python Main.py --env_type dev --frequency daily --schedule
```

The `--sources` flag enables selective execution of individual pipeline phases, and `--tickers` restricts to specific symbols. The `--schedule` flag starts an APScheduler cron job for automated recurring runs.

---

## Dependency Management (Poetry)

All dependencies are managed via **Poetry** and defined in `pyproject.toml`:

**Production dependencies** (14 packages):

| Package | Version | Purpose |
|---------|---------|---------|
| `yfinance` | ^0.2.36 | Yahoo Finance market data API |
| `pandas` | ^2.2.0 | DataFrames for data processing |
| `numpy` | ^1.26.0 | Numerical operations |
| `sqlalchemy` | ^2.0.38 | ORM + PostgreSQL upsert queries |
| `psycopg2-binary` | ^2.9.9 | PostgreSQL adapter |
| `pydantic` | ^2.10.0 | Data validation models |
| `pydantic-settings` | ^2.1.0 | Environment configuration |
| `ruamel-yaml` | ^0.18.0 | YAML config parsing |
| `rich` | ^13.7.0 | Terminal progress display |
| `pymongo` | ^4.6.0 | MongoDB document store |
| `confluent-kafka` | ^2.3.0 | Kafka event streaming |
| `apscheduler` | ^3.10.0 | Cron-based scheduling |
| `lseg-data` | ^2.0 | LSEG/Refinitiv ESG data |
| `ift-global` | git | Shared library (logging, config, MinIO) |

**Development dependencies** (8 packages): `pytest`, `pytest-cov`, `pytest-mock`, `flake8`, `black`, `isort`, `bandit`, `safety`, `sphinx`, `pydata-sphinx-theme`.

```bash
# Install all dependencies
poetry install

# Add a new dependency
poetry add <package>

# Update lock file
poetry lock

# Export requirements.txt (for non-Poetry environments)
poetry export -f requirements.txt --output requirements.txt
```

---

## Documentation (Sphinx)

Full project documentation is generated using **Sphinx** with the `pydata-sphinx-theme` and `autodoc` extensions. Documentation covers:

- **Installation guide** — prerequisites, Docker setup, environment variables
- **Usage guide** — CLI reference, frequency lookback table, common examples
- **Architecture overview** — system diagram, data flow, module structure, database schema, design patterns
- **API reference** — auto-generated from docstrings for all 30+ modules

```bash
# Build HTML documentation
cd docs/
poetry run sphinx-build -b html . _build/html

# View documentation
open _build/html/index.html
```

---

## Docker Infrastructure

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres_db` | `postgres:14` | 5438 | Primary relational store |
| `minio` | `minio/minio` | 9000 / 9001 | Object store + console |
| `mongo` | `mongo:7.0` | 27017 | Document store |
| `zookeeper` | `confluentinc/cp-zookeeper:7.6.0` | 2181 | Kafka coordination |
| `kafka` | `confluentinc/cp-kafka:7.6.0` | 9092 | Event streaming |
| `pgadmin` | `dpage/pgadmin4` | 5050 | PostgreSQL GUI |

```bash
docker compose up --build -d    # Start all
docker compose down             # Stop all
docker compose down -v          # Stop and reset data
```
