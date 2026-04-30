<div align="center">

# Systematic Equity Data Pipeline

### Production-Grade ETL for Multi-Factor Quantitative Research

*678 equities &middot; 11 data streams &middot; 6 APIs &middot; triple-database architecture &middot; 1,221 tests*

<br>

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Kafka](https://img.shields.io/badge/Kafka-3.0-231F20?style=for-the-badge&logo=apachekafka&logoColor=white)](https://kafka.apache.org/)
[![Tests](https://img.shields.io/badge/Tests-1221_passed-brightgreen?style=for-the-badge)](test/)
[![Coverage](https://img.shields.io/badge/Coverage-94%25-brightgreen?style=for-the-badge)](test/)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

[![GitHub stars](https://img.shields.io/github/stars/abailey81/Big-Data-Pipeline?style=social)](https://github.com/abailey81/Big-Data-Pipeline/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/abailey81/Big-Data-Pipeline?style=social)](https://github.com/abailey81/Big-Data-Pipeline/network/members)

---

**Production-grade ETL pipeline** ingesting 6+ years of financial market data for 678 publicly listed companies across US, UK, European, Canadian, and Swiss exchanges. Triple-database storage (PostgreSQL + MongoDB + MinIO) with Apache Kafka event streaming.

<br>

[Data Sources](#data-sources) &middot; [Architecture](#architecture) &middot; [Quick Start](#quick-start) &middot; [Database Schema](#database-schema) &middot; [Testing](#testing)

</div>

<br>

## Highlights

<table>
<tr>
<td align="center" width="25%">
<br>
<strong>11 Data Streams</strong>
<br><br>
Prices, fundamentals, EDGAR, FX, VIX, RFR, ESG, sentiment, ratios, historical ratios, benchmarks
<br><br>
</td>
<td align="center" width="25%">
<br>
<strong>Triple Database</strong>
<br><br>
PostgreSQL (12 tables) + MongoDB (documents) + MinIO (data lake) with Kafka streaming
<br><br>
</td>
<td align="center" width="25%">
<br>
<strong>Resilience Engineering</strong>
<br><br>
Circuit breaker, token-bucket rate limiter, exponential backoff, and graceful degradation
<br><br>
</td>
<td align="center" width="25%">
<br>
<strong>1,221 Tests</strong>
<br><br>
1,221 tests across unit, integration, and end-to-end tiers with Bandit security scanning
<br><br>
</td>
</tr>
</table>

---

## Data Sources

| # | Source | API | Coverage | Smart Skip |
|---|--------|-----|----------|------------|
| 1 | Daily prices (OHLCV + adjusted close) | Yahoo Finance | 678 symbols, 6 years | -- |
| 2 | Quarterly / annual fundamentals | Yahoo Finance | 606 / 678 symbols | -- |
| 3 | EDGAR supplementary fundamentals | SEC EDGAR XBRL | 430 US tickers, 5+ years | Skips non-US |
| 4 | Company financial ratios (snapshot) | Yahoo Finance | 678 / 678 symbols | -- |
| 5 | Historical ratios (6-year time-series) | Computed from fundamentals + prices | 603 / 678 symbols | -- |
| 6 | FX rates (GBP, EUR, CAD, CHF to USD) | Yahoo Finance | 4 / 4 pairs, 6 years | -- |
| 7 | CBOE Volatility Index (VIX) | Yahoo Finance | 2020--2026 | -- |
| 8 | US 3-Month Treasury rate (DGS3MO) | FRED | 2020--2026 | -- |
| 9 | Regional benchmark indices (5) | Yahoo Finance | S&P 500, FTSE 100, Euro Stoxx 50, TSX, SMI | -- |
| 10 | ESG sustainability scores | LSEG Data Platform | 234 / 678 symbols (API ceiling) | -- |
| 11 | News sentiment (VADER + financial boost) | yfinance + NewsAPI + GDELT | 674 / 678 symbols | Recent articles only |

**Date range:** 2020-02-27 to present (6-year lookback by default, configurable via `conf.yaml`)

**Key design choices:**
- All database writes use `ON CONFLICT DO UPDATE` for idempotent re-runs
- ESG provides current-day snapshot only (LSEG API limitation — no historical data available)
- Sentiment covers recent articles (~30 days) via the 3-source cascade; historical depth is not backfilled
- Company additions/removals are handled automatically by re-querying `company_static` at each run

---

## Architecture

```
                         +---------------------------+
                         |      Main.py (ETL)        |
                         |   Parallel orchestration  |
                         +-------------+-------------+
                                       |
          +----------+---------+-------+-------+---------+
          |          |         |       |       |         |
     +----v---+ +---v----+ +-v----+ +-v----+ +-v-----+
     | Yahoo  | |  SEC   | | FRED | | LSEG | | GDELT |
     |Finance | | EDGAR  | |T-Bill| |(ESG) | | News  |
     +----+---+ +---+----+ +--+---+ +--+---+ +--+----+
          |          |         |        |        |
          +----------+---------+---+----+--------+
                                   |
                     +-------------v--------------+
                     |      Data Cleaning          |
                     | Pydantic validation + DQ    |
                     +-------------+--------------+
                                   |
          +------------------------+------------------------+
          |                        |                        |
   +------v------+          +------v------+          +------v------+
   |  PostgreSQL  |          |   MongoDB   |          |    MinIO    |
   | 12 tables    |          | (documents) |          | (data lake) |
   +------+-------+          +-------------+          +-------------+
          |
   +------v------+
   |    Kafka    |
   | (6 topics)  |
   +-------------+
```

**Orchestration groups (parallel execution with smart cascade):**

| Group | Sources | Parallelism | Smart Skip Logic |
|-------|---------|-------------|------------------|
| A (parallel) | Prices + Fundamentals | 2 threads, launched at t=0 | -- |
| Independent (parallel) | FX + RFR + ESG + Sentiment | 4 threads, launched at t=0 | -- |
| A.5 | EDGAR (US tickers) | After Group A | Skips non-US tickers |
| B (sequential) | VIX + Benchmark | Sequential (yfinance thread-safety) | -- |
| C | Company Ratios (snapshots) | 8 parallel workers | Skips inactive tickers |
| D | Historical Ratios (computed) | 8 parallel workers, DB-only | -- |

---

## Quick Start

**1. Start the class infrastructure**

From the root of the `ift_coursework_2025` repository, start the required services:

```bash
docker compose up -d --build
```

> If `docker` is not found, Docker Desktop may not be in your PATH. Fix with:
> ```bash
> sudo ln -s /Applications/Docker.app/Contents/Resources/bin/docker /usr/local/bin/docker
> ```
> Or on Linux: `sudo apt install docker-compose-plugin`

This starts PostgreSQL (port 5439), MongoDB (port 27019), MinIO (port 9000),
Kafka (port 9092), Zookeeper (port 2181), and pgAdmin (port 5051),
and seeds the `fift` database with the `company_static` table (678 equities).

**2. Install dependencies**

Navigate to the `team_kolmogorov/coursework_one/` directory:

```bash
cd team_kolmogorov/coursework_one
```

Install Poetry if not already installed (pick one):

```bash
brew install poetry
```
> Alternatives: `pipx install poetry` or `pip install --user poetry`

Then install project dependencies:

```bash
poetry install
```

**3. Configure environment**

```bash
cp .env.example .env.dev
# Edit .env.dev with your API keys (all optional — pipeline degrades gracefully):
#   NEWSAPI_KEY              (free at newsapi.org)
#   REFINITIV_USERNAME       (LSEG platform — ESG scores)
#   REFINITIV_PASSWORD
#   REFINITIV_APP_KEY
```

**4. Run the pipeline**

```bash
# Full 6-year backfill:
poetry run python Main.py --env_type dev

# Daily incremental update:
poetry run python Main.py --env_type dev --frequency daily

# Custom date range:
poetry run python Main.py --env_type dev --start_date 2023-01-01 --end_date 2024-12-31

# Subset of sources:
poetry run python Main.py --env_type dev --sources prices fundamentals fx

# Subset of sources
poetry run python Main.py --env_type dev --sources prices fundamentals fx
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
| `--schedule` | false | Run on recurring schedule via APScheduler |

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
| `company_ratios` | `(symbol, snapshot_date, field_name)` | 57 financial ratios (EAV) |
| `esg_scores` | `(symbol, cob_date)` | Total ESG + component scores |
| `news_sentiment` | `(symbol, cob_date)` | VADER composite score + dispersion |
| `ingestion_log` | `log_id` | Audit trail for every download attempt |
| `pipeline_metadata` | `(data_source, symbol)` | Last successful run per source |

All tables use `ON CONFLICT DO UPDATE` for idempotent re-runs.

---

## Design Patterns

| Pattern | Implementation | Reference |
|---------|---------------|-----------|
| **Template Method** | `BaseDownloader` defines workflow; subclasses override `_execute_download()` | Gamma et al. (1994) |
| **Circuit Breaker** | Three-state machine (CLOSED / OPEN / HALF_OPEN) prevents cascading failures | Nygard (2007) |
| **Token Bucket** | Rate limiter controls API request rate with burst capacity | Turner (1986) |
| **MapReduce** | `ThreadPoolExecutor` distributes per-ticker downloads; PostgreSQL aggregates via upsert | Dean & Ghemawat (2004) |
| **EAV** | `fundamentals` and `company_ratios` tables store flexible metrics without schema migration | |
| **Graceful Degradation** | MinIO, MongoDB, and Kafka failures are logged but do not halt the pipeline | |

---

## Sentiment Scoring

**3-source news cascade** (per ticker, parallel across 6 workers):
1. **yfinance `Ticker.news`** -- primary (no API key needed)
2. **NewsAPI `/v2/everything`** -- secondary gap-fill (requires `NEWSAPI_KEY`)
3. **GDELT DOC API** -- tertiary gap-fill (free, no key)

**Composite score (0--100):**

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

**1,221 tests** across 34 test files | **94% coverage**

```
poetry run pytest ./test/ --cov=modules --cov-report=term-missing
======================= 1221 passed =========================
```

Three-tier testing strategy:

| Tier | Description | Infrastructure |
|------|-------------|----------------|
| **Unit** | Individual modules with mocked external dependencies | None required |
| **Integration** | PostgreSQL upsert idempotency and schema checks | Docker (auto-skipped if unavailable) |
| **End-to-End** | Full pipeline workflows from CLI to database | Mocked at boundaries |

```bash
# Full test suite
poetry run pytest ./test/

# Unit tests only
poetry run pytest ./test/ -m "not integration"

# With HTML coverage report
poetry run pytest ./test/ --cov-report=html
```

---

## Project Structure

```
Big-Data-Pipeline/
├── Main.py                          # Pipeline entry point and orchestrator
├── pyproject.toml                   # Poetry dependencies and tool config
├── docker-compose.yml               # Infrastructure services (6 containers + 3 seed)
├── config/
│   └── conf.yaml                    # Pipeline configuration (dev + docker)
├── modules/
│   ├── orchestration/               # Pipeline stage orchestration (v3.2.0)
│   │   ├── state.py                 # Shared state, utilities, health checks
│   │   ├── stage_prices.py          # Daily OHLCV ingestion stage
│   │   ├── stage_fundamentals.py    # Yahoo Finance + SEC EDGAR
│   │   ├── stage_macro.py           # FX, VIX, risk-free rate, benchmarks
│   │   ├── stage_ratios.py          # 57-field ratio computation engine
│   │   ├── stage_esg.py             # ESG sustainability scores (LSEG)
│   │   └── stage_sentiment.py       # News sentiment (yfinance + NewsAPI + GDELT)
│   ├── input/                       # Data source downloaders
│   │   ├── base_downloader.py       # Abstract base (circuit breaker, retry)
│   │   ├── price_downloader.py      # Daily OHLCV for 678 equities
│   │   ├── fundamentals_downloader.py  # yfinance quarterly/annual
│   │   ├── edgar_downloader.py      # SEC EDGAR XBRL filings (US)
│   │   ├── finnhub_downloader.py    # Finnhub fundamentals (disabled — free tier US-only)
│   │   ├── fmp_downloader.py        # FMP fundamentals (disabled — low coverage)
│   │   ├── simfin_downloader.py     # SimFin fundamentals (disabled — low coverage)
│   │   ├── alphavantage_downloader.py  # Alpha Vantage (disabled — low coverage)
│   │   ├── fx_downloader.py         # FX rate pairs
│   │   ├── vix_downloader.py        # CBOE Volatility Index
│   │   ├── risk_free_rate_downloader.py  # FRED DGS3MO T-bill rate
│   │   ├── esg_downloader.py        # ESG sustainability scores (LSEG batch)
│   │   ├── news_downloader.py       # News articles (3-source cascade)
│   │   ├── gdelt_downloader.py      # GDELT DOC API (sentiment gap-fill)
│   │   ├── newsapi_downloader.py    # NewsAPI (secondary news source)
│   │   └── get_company_static.py    # 678-company universe
│   ├── processing/                  # Data cleaning and transformation
│   │   ├── data_cleaner.py          # Pydantic validation
│   │   ├── data_quality.py          # Post-clean quality checks
│   │   ├── sentiment_scorer.py      # VADER + financial domain boost
│   │   └── ticker_utils.py          # Currency mapping, Swiss remap
│   ├── output/                      # Data export utilities
│   │   └── data_exporter.py         # Query by company or year
│   ├── db_ops/                      # Database clients
│   │   ├── sql_conn.py              # PostgreSQL (SQLAlchemy)
│   │   ├── mongo_conn.py            # MongoDB (PyMongo)
│   │   ├── minio_store.py           # MinIO S3-compatible store
│   │   └── kafka_ops.py             # Kafka producer/consumer
│   ├── data_models/                 # Pydantic + SQLAlchemy ORM
│   └── utils/                       # Infrastructure utilities
│       ├── args_parser.py           # CLI argument parser
│       ├── circuit_breaker.py       # Three-state resilience pattern
│       ├── concurrent_executor.py   # ThreadPoolExecutor wrapper
│       ├── exceptions.py            # Custom pipeline exceptions
│       ├── health_check.py          # Pre-flight system health checks
│       ├── info_logger.py           # Centralised pipeline logging
│       ├── pipeline_metrics.py      # Observability metrics
│       ├── progress_tracker.py      # Rich animated progress bars
│       ├── rate_limiter.py          # Token-bucket rate limiting
│       ├── retry.py                 # Exponential backoff decorator
│       └── scheduler.py            # APScheduler integration
├── .coveragerc                      # Coverage measurement configuration
├── .flake8                          # Flake8 linting rules
├── .env.example                     # Environment variable template
├── .github/workflows/
│   └── ci.yml                       # CI/CD: lint, test, security scan
├── static/schema/
│   ├── create_tables.sql            # PostgreSQL DDL (12 tables)
│   └── company_static.csv           # Universe of 678 tickers
├── test/                            # 1,221 tests, 94% coverage
├── docs/                            # Sphinx documentation
└── reports/                         # Security scan results
```

---

## Docker Infrastructure

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres_db` | postgres:16-alpine | 5439 | Primary relational store (database: `fift`) |
| `mongo_db` | mongo:7.0 | 27019 | Document store |
| `miniocw` | minio/minio | 9000 / 9001 | Object store (bucket: `csreport`) + console |
| `zookeeper` | cp-zookeeper:7.6.0 | 2181 | Kafka cluster coordination |
| `kafka` | cp-kafka:7.6.0 | 9092 | Event streaming (6 topics) |
| `pgadmin` | dpage/pgadmin4 | 5051 | PostgreSQL GUI |

```bash
# Start all services:
docker compose up -d --build

# Stop all:
docker compose down

# Stop and reset data:
docker compose down -v
```

---

<div align="center">

**[MIT License](LICENSE)**

Built with SQLAlchemy, Pydantic, yfinance, confluent-kafka, and Poetry

</div>
