# CW1: Data Pipeline for Flow-Based Multi-Factor Equity Strategy

**UCL Institute of Finance & Technology — IFTE0003: Big Data in Quantitative Finance**
**Team XX** | Version 2.0.0

---

## Overview

This project implements a production-grade ETL pipeline that ingests six years of financial market data for **678 publicly listed companies** across US, UK, European, Canadian, and Swiss exchanges. The pipeline stores data in a triple-database architecture (PostgreSQL, MongoDB, MinIO) and streams events to Apache Kafka for downstream factor construction.

The data infrastructure supports a **flow-based multi-factor equity strategy** grounded in Vayanos and Woolley (2012), targeting momentum, value, and quality signals arising from institutional fund flows.

---

## Data Sources

| # | Source | API | Records | Coverage |
|---|--------|-----|---------|----------|
| 1 | Daily prices (OHLCV + adjusted close) | Yahoo Finance | ~994k rows | 672 / 678 symbols |
| 2 | Quarterly / annual fundamentals | Yahoo Finance + SEC EDGAR | ~205k rows | 606 / 678 symbols |
| 3 | EDGAR supplementary fundamentals | SEC EDGAR XBRL | ~137k rows | 436 US symbols |
| 4 | Company financial ratios | Yahoo Finance + Finnhub | ~74k rows | 636 / 678 symbols |
| 5 | FX rates (GBP, EUR, CAD, CHF → USD) | Yahoo Finance | ~6.3k rows | 4 / 4 pairs |
| 6 | CBOE Volatility Index (VIX) | Yahoo Finance | ~1.5k rows | 2020–2026 |
| 7 | US 3-Month Treasury rate (DGS3MO) | FRED | ~1.6k rows | 2020–2026 |
| 8 | Regional benchmark indices (5) | Yahoo Finance | ~8k rows | S&P 500, FTSE 100, Euro Stoxx 50, TSX, SMI |
| 9 | ESG sustainability scores | LSEG / yfinance | 234 rows | ~35% (API ceiling) |
| 10 | News sentiment (VADER + financial boost) | yfinance + NewsAPI + GDELT | ~1.9k rows | 621 / 678 symbols |
| 11 | Computed ratios (B/P, E/P, CF/P, ROE, D/E) | Derived from sources 2 + 4 | per-ticker | 636 / 678 symbols |

**Date range:** 2020-02-27 → present (6-year backfill by default)

---

## Architecture

```
                          ┌─────────────────────────┐
                          │      Main.py (ETL)       │
                          │   Parallel orchestration │
                          └───────────┬─────────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          │                           │                           │
   ┌──────▼──────┐            ┌───────▼──────┐           ┌───────▼──────┐
   │ Yahoo Finance│            │  SEC EDGAR   │           │    FRED API  │
   │  yfinance   │            │  XBRL EDGAR  │           │  (DGS3MO)    │
   └──────┬──────┘            └───────┬──────┘           └───────┬──────┘
          │                           │                           │
          └───────────────────────────┼───────────────────────────┘
                                      │
                     ┌────────────────▼────────────────┐
                     │         Data Cleaning           │
                     │  Pydantic models + DQ checks    │
                     └────────────────┬────────────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          │                           │                           │
   ┌──────▼──────┐            ┌───────▼──────┐           ┌───────▼──────┐
   │  PostgreSQL  │            │   MongoDB    │           │    MinIO     │
   │  (primary)   │            │ (semi-struct)│           │ (data lake)  │
   └─────────────┘            └─────────────┘           └─────────────┘
          │
   ┌──────▼──────┐
   │    Kafka    │
   │  (streaming)│
   └─────────────┘
```

**Orchestration groups (parallel execution):**

| Group | Sources | Notes |
|-------|---------|-------|
| A (parallel) | Prices + Fundamentals | Launched at t=0 |
| Independent (parallel) | FX + RFR + ESG + Sentiment | Launched at t=0, run concurrently with Group A |
| A.5 + A.6 (parallel) | EDGAR (US) + Finnhub (non-US) | Start after Group A joins |
| B | VIX + Benchmark | Sequential; yfinance thread-safety constraint |
| C | Company Ratios | 3-source waterfall; 8 parallel workers |

---

## Prerequisites

| Dependency | Version | Purpose |
|------------|---------|---------|
| Python | 3.11+ | Runtime |
| PostgreSQL | 14+ | Primary data store (port 5438 in dev) |
| MongoDB | 6+ | Semi-structured document store |
| Apache Kafka | 3+ | Event streaming |
| MinIO | Latest | S3-compatible object store (data lake) |
| Docker + Compose | 24+ | Infrastructure (all services containerised) |

---

## Quick Start

**1. Clone and install dependencies**

```bash
git clone <repo-url>
cd coursework_one
pip install poetry
poetry install
```

**2. Start infrastructure**

```bash
docker compose up --build -d
```

This starts PostgreSQL (port 5438), MongoDB (port 27017), MinIO (port 9000), and Kafka (port 9092).

**3. Configure environment**

```bash
cp .env.example .env.dev
# Edit .env.dev — set FINNHUB_API_KEY (free at finnhub.io)
# Optionally set NEWSAPI_KEY (free at newsapi.org/register) for news gap-fill
```

**4. Initialise the database schema**

```bash
poetry run python Main.py --env_type dev --init_schema
```

**5. Run the full 6-year backfill**

```bash
poetry run python Main.py --env_type dev
```

**6. Run daily incremental update**

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
| `--frequency` | `None` | `daily` (5d), `weekly` (14d), `monthly` (35d), `quarterly` (95d). Omit for full 6-year backfill. |
| `--start_date` | derived | Override start date (YYYY-MM-DD) |
| `--end_date` | today | Override end date (YYYY-MM-DD) |
| `--sources` | all | Space-separated subset: `prices fundamentals fx vix risk_free_rate benchmark ratios esg sentiment` |
| `--tickers` | all 678 | Override universe with specific tickers |
| `--init_schema` | false | Create/update PostgreSQL schema before running |
| `--dry_run` | false | Validate configuration without downloading |
| `--schedule` | false | Run on recurring APScheduler cron schedule |

**Examples:**

```bash
# Full 6-year backfill (default — ~18 min)
poetry run python Main.py --env_type dev

# Custom lookback: 3 years
poetry run python Main.py --env_type dev --start_date 2023-01-01

# Custom lookback: 10 years
poetry run python Main.py --env_type dev --start_date 2016-01-01

# Specific date range
poetry run python Main.py --env_type dev --start_date 2022-01-01 --end_date 2024-12-31

# Daily incremental (last 5 trading days)
poetry run python Main.py --env_type dev --frequency daily

# Weekly incremental (last 14 days)
poetry run python Main.py --env_type dev --frequency weekly

# Prices + fundamentals only, custom range
poetry run python Main.py --env_type dev --sources prices fundamentals --start_date 2020-01-01

# Run in background with logging
poetry run python Main.py --env_type dev > /tmp/pipeline.log 2>&1 &
```

> **Lookback logic:** If `--frequency` is provided, the lookback window is fixed (`daily`=5d, `weekly`=14d, `monthly`=35d, `quarterly`=95d). If `--start_date` is provided, it is used directly. If neither is provided, the pipeline defaults to a **6-year full backfill** (`lookback_years: 6` in `conf.yaml`). To change the default backfill length, edit `lookback_years` in `config/conf.yaml`.

---

## Project Structure

```
coursework_one/
├── Main.py                          # Pipeline entry point and orchestrator
├── config/
│   └── conf.yaml                    # Environment configuration (dev + docker)
├── modules/
│   ├── input/                       # Downloaders (one per data source)
│   │   ├── price_downloader.py
│   │   ├── fundamentals_downloader.py
│   │   ├── edgar_downloader.py
│   │   ├── finnhub_downloader.py
│   │   ├── fx_downloader.py
│   │   ├── vix_downloader.py
│   │   ├── risk_free_rate_downloader.py
│   │   ├── benchmark_downloader.py
│   │   ├── ratios_downloader.py
│   │   ├── esg_downloader.py
│   │   ├── news_downloader.py
│   │   └── newsapi_downloader.py
│   ├── processing/                  # Data cleaning and transformation
│   │   ├── data_cleaner.py
│   │   ├── data_quality.py
│   │   └── sentiment_scorer.py      # VADER + financial domain boost
│   ├── db_ops/                      # Database clients
│   │   ├── sql_conn.py              # PostgreSQL (SQLAlchemy + psycopg2)
│   │   ├── mongo_conn.py            # MongoDB (PyMongo)
│   │   ├── minio_store.py           # MinIO object store
│   │   └── kafka_ops.py             # Kafka producer/consumer
│   ├── data_models/
│   │   └── table_models.py          # SQLAlchemy ORM table definitions
│   └── utils/
│       ├── args_parser.py           # CLI argument parser
│       ├── circuit_breaker.py       # Resilience pattern
│       ├── rate_limiter.py          # Token-bucket rate limiting
│       ├── retry.py                 # Exponential backoff decorator
│       ├── concurrent_executor.py   # ThreadPoolExecutor wrapper
│       ├── health_check.py          # Pre-flight dependency checks
│       ├── pipeline_metrics.py      # Timing and outcome tracking
│       ├── progress_tracker.py      # Rich terminal progress display
│       ├── scheduler.py             # APScheduler cron integration
│       └── info_logger.py           # Structured logging setup
├── static/
│   └── schema/
│       └── create_tables.sql        # PostgreSQL DDL (12 tables)
├── tests/                           # Pytest test suite (756+ tests)
├── docker-compose.yml               # Full infrastructure (8 services)
├── pyproject.toml
└── CHANGELOG.md
```

---

## Database Schema

**PostgreSQL** (`fift` database, `systematic_equity` schema):

| Table | Primary Key | Description |
|-------|-------------|-------------|
| `company_static` | `symbol` | Universe of 678 companies |
| `daily_prices` | `(symbol, cob_date)` | OHLCV + adjusted close |
| `fundamentals` | `(symbol, report_date, field_name, period_type)` | EAV balance sheet / income statement |
| `fx_rates` | `(currency_pair, cob_date)` | GBP, EUR, CAD, CHF → USD |
| `vix_data` | `cob_date` | CBOE VIX daily |
| `risk_free_rate` | `cob_date` | US 3-Month T-Bill (DGS3MO) |
| `benchmark_index` | `(symbol, cob_date)` | 5 regional indices |
| `company_ratios` | `(symbol, snapshot_date, field_name)` | P/E, P/B, ROE, margins, etc. (EAV) |
| `esg_scores` | `(symbol, cob_date)` | Total ESG + component scores |
| `news_sentiment` | `(symbol, cob_date)` | VADER composite score + dispersion |
| `ingestion_log` | `log_id` | Audit trail for every download attempt |
| `pipeline_metadata` | `(data_source, symbol)` | Last successful run per source |

All tables use `INSERT ... ON CONFLICT DO UPDATE` (upsert) for idempotent re-runs.

**MongoDB** (`ift_cw1` database):
- `raw_prices`, `raw_fundamentals`, `raw_fx`, `raw_macro`, `raw_benchmark`, `raw_ratios` — raw API responses
- `esg_reports`, `news_sentiment` — semi-structured ESG and news documents

**MinIO** (`iftbigdata` bucket, `raw-data/` prefix):
- Parquet/CSV/JSON backups organised by source and ticker

---

## Sentiment Scoring

**3-source news cascade** (per ticker, parallel across 6 workers):
1. **yfinance `Ticker.news`** — primary (no API key needed)
2. **NewsAPI `/v2/everything`** — secondary gap-fill (requires `NEWSAPI_KEY`, free tier: 100 req/day)
3. **GDELT DOC API** — tertiary gap-fill (free, no key)

Each source triggers only when the previous returns zero articles.

The sentiment pipeline uses a dual-layer NLP model (Hutto & Gilbert, 2014; Tetlock, 2007):

**Layer 1 — VADER:** Rule-based compound score [-1, +1] handling negation, punctuation intensity, and capitalisation.

**Layer 2 — Financial boost:** Domain-specific signed deltas for financial vocabulary VADER underweights:
- `beat` / `beats` → +0.22 | `miss` / `misses` → -0.22
- `upgraded` → +0.22 | `downgraded` → -0.22
- `bankruptcy` → -0.32 | `fraud` → -0.26
- 20+ multi-word phrases: `beat estimates` +0.24, `chapter 11` -0.35, etc.

**Composite score (0–100, stored as `sentiment_score`):**

```
sentiment_score = vader_component   × 0.45   # VADER signal (rescaled 0-100)
                + positive_ratio    × 0.25   # Fraction of positive articles
                + volume_component  × 0.20   # Coverage (saturates at 20 articles)
                + agreement_bonus   × 0.10   # Reward for low inter-article dispersion
```

`score_dispersion` (std dev of per-article scores) is stored as a standalone CW2 factor for market disagreement analysis.

---

## Resilience

| Mechanism | Implementation | Purpose |
|-----------|---------------|---------|
| Circuit breaker | `modules/utils/circuit_breaker.py` | Stops retrying a broken API |
| Token-bucket rate limiter | `modules/utils/rate_limiter.py` | Prevents rate limit breaches |
| Exponential backoff | `modules/utils/retry.py` | Jittered retry on transient failures |
| Per-batch download timeout | Daemon thread + `join(timeout=90)` | Prevents stuck HTTP sockets blocking prices |
| Thread-join hard caps | `join(timeout=N)` on all three join loops | Prevents any single hung thread from blocking the pipeline indefinitely |
| Kafka fire-and-forget | `threading.Thread(daemon=True)` | Kafka ACK latency never blocks DB writes |
| MongoDB socket timeout | `socketTimeoutMS=30000` | Prevents indefinite socket hangs |
| Upsert-safe writes | `ON CONFLICT DO UPDATE` | Idempotent re-runs without duplicates |
| SIGINT / SIGTERM handler | `signal.signal(...)` | Graceful shutdown between stages |

---

## Testing

```bash
# Run full test suite
poetry run pytest ./tests/ -v

# Unit tests only (no external dependencies)
poetry run pytest ./tests/ -m "not integration" -v

# With coverage report
poetry run pytest ./tests/ --cov=modules --cov-report=term-missing --cov-report=html

# Code quality checks
poetry run flake8 modules/
poetry run black --check modules/
poetry run isort --check-only modules/
poetry run bandit -r modules/ -c pyproject.toml
```

**790+ tests** across 25 test files. Coverage: 82%+.

---

## Configuration

`config/conf.yaml` controls all runtime parameters:

```yaml
dev:
  params:
    Pipeline:
      lookback_years: 6          # Default backfill window (used when --frequency is omitted)
      api_delay_seconds: 0.5     # Base delay between API requests
      max_retries: 3             # Maximum retry attempts per request
      backoff_base: 2.0          # Exponential backoff multiplier
      batch_size: 50             # Tickers per yfinance batch download
```

---

## Docker Infrastructure

`docker-compose.yml` provisions eight services:

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres_db` | `postgres:14` | 5438 | Primary relational store |
| `minio` | `minio/minio` | 9000 / 9001 | Object store + console |
| `mongo` | `mongo:7.0` | 27017 | Document store |
| `zookeeper` | `confluentinc/cp-zookeeper:7.6.0` | 2181 | Kafka coordination |
| `kafka` | `confluentinc/cp-kafka:7.6.0` | 9092 | Event streaming |
| `mongo-seed` | Custom init | — | Creates MongoDB collections + indexes |
| `minio-init` | `minio/mc` | — | Creates MinIO bucket |
| `pgadmin` | `dpage/pgadmin4` | 5050 | PostgreSQL GUI |

```bash
docker compose up --build -d  # Start all services (rebuild images)
docker compose down           # Stop all services
docker compose down -v        # Stop and remove volumes (data reset)
```

---

## Accessing Data

**PostgreSQL (direct query):**

```python
import psycopg2
conn = psycopg2.connect(host='localhost', port=5438, dbname='fift',
                        user='postgres', password='postgres')
cur = conn.cursor()
cur.execute("""
    SELECT symbol, cob_date, adj_close_price
    FROM systematic_equity.daily_prices
    WHERE symbol = 'AAPL'
    ORDER BY cob_date DESC
    LIMIT 10
""")
```

**SQLAlchemy ORM:**

```python
from modules.db_ops.sql_conn import DatabaseMethods
db = DatabaseMethods(host='localhost', port=5438, dbname='fift',
                     username='postgres', password='postgres',
                     schema='systematic_equity')
```

**PgAdmin:** `http://localhost:5050`

**MinIO Console:** `http://localhost:9001`

