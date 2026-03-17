<div align="center">

# Systematic Equity Data Pipeline

### Production-Grade ETL for Multi-Factor Quantitative Research

*678 equities &middot; 14 data streams &middot; 11 APIs &middot; triple-database architecture &middot; 756 tests*

<br>

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Kafka](https://img.shields.io/badge/Kafka-3.0-231F20?style=for-the-badge&logo=apachekafka&logoColor=white)](https://kafka.apache.org/)
[![Tests](https://img.shields.io/badge/Tests-756_passed-brightgreen?style=for-the-badge)](test/)
[![Coverage](https://img.shields.io/badge/Coverage-83%25-brightgreen?style=for-the-badge)](test/)
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
<strong>14 Data Streams</strong>
<br><br>
Prices, fundamentals, EDGAR, FMP, SimFin, Alpha Vantage, FX, VIX, RFR, ESG, sentiment, ratios, historical ratios, sentiment backfill
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
<strong>83% Test Coverage</strong>
<br><br>
756 tests across unit, integration, and end-to-end tiers with Bandit security scanning
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
| 3 | EDGAR supplementary fundamentals | SEC EDGAR XBRL | US tickers, 5+ years | Skips non-US |
| 4 | Finnhub supplementary fundamentals | Finnhub | Non-US tickers | Skips US |
| 5 | Non-US fundamentals supplement | FMP + SimFin + Alpha Vantage | Non-US tickers, cascade | Skips tickers with 20+ quarterly records |
| 6 | Company financial ratios (snapshot) | Yahoo Finance + Finnhub | 637 / 678 symbols | -- |
| 7 | Historical ratios (6-year time-series) | Computed from fundamentals + prices | All tickers with data | Skips tickers with 20+ `_hist` records |
| 8 | FX rates (GBP, EUR, CAD, CHF to USD) | Yahoo Finance | 4 / 4 pairs, 6 years | -- |
| 9 | CBOE Volatility Index (VIX) | Yahoo Finance | 2020--2026 | -- |
| 10 | US 3-Month Treasury rate (DGS3MO) | FRED | 2020--2026 | -- |
| 11 | Regional benchmark indices (5) | Yahoo Finance | S&P 500, FTSE 100, Euro Stoxx 50, TSX, SMI | -- |
| 12 | ESG sustainability scores | LSEG Data Platform | ~34% (API ceiling) | -- |
| 13 | News sentiment (VADER + financial boost) | yfinance + NewsAPI + GDELT | 667 / 678 symbols | -- |
| 14 | Sentiment historical backfill | GDELT quarterly archive | 6-year quarterly coverage | Skips tickers with 4+ records |

**Date range:** 2020-02-27 to present (6-year backfill by default)

**Smart cascade logic:** Each source checks the database before downloading. If prior sources already provided sufficient data for a ticker, it is skipped -- zero wasted API calls. All database writes use `ON CONFLICT DO UPDATE` for idempotent re-runs.

---

## Architecture

```
                              +---------------------------+
                              |      Main.py (ETL)        |
                              |   Parallel orchestration  |
                              +-------------+-------------+
                                            |
    +--------+--------+--------+-------+-------+-------+--------+-------+--------+
    |        |        |        |       |       |       |        |       |        |
  +-v-----+ +-v----+ +-v----+ +-v---+ +-v---+ +-v---+ +-v----+ +-v---+ +-v----+
  |Yahoo  | | SEC  | |Finn- | |FMP | |Sim- | |Alpha| |FRED  | |LSEG | |GDELT |
  |Finance| |EDGAR | |hub   | |    | |Fin  | |Vant.| |T-Bill| |(ESG)| |News  |
  +-------+ +------+ +------+ +----+ +-----+ +-----+ +------+ +-----+ +------+
    |        |        |        |       |       |       |        |       |
    +--------+--------+--------+---+---+-------+-------+--------+-------+
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
| A.5-A.7 (parallel) | EDGAR (US) + Finnhub (non-US) + **FMP/SimFin/AV (non-US)** | 3 threads, start after Group A | Non-US supplement skips tickers with 20+ quarterly records |
| B (sequential) | VIX + Benchmark | Sequential (yfinance thread-safety) | -- |
| C | Company Ratios (snapshots) | 8 parallel workers | Skips inactive tickers |
| D | **Historical Ratios** (computed) | 8 parallel workers, DB-only | Skips tickers with 20+ existing `_hist` records |
| E | **Sentiment Backfill** (GDELT archive) | 4 parallel workers | Skips tickers with 4+ sentiment records; skips quarters already in DB |

**Non-US fundamentals cascade** (Group A.7): For each ticker missing quarterly depth, tries FMP first (fastest, 250 req/day). If FMP returns nothing, SimFin tries immediately on the same thread (2000 req/day). If SimFin also fails, Alpha Vantage tries with 8-key fallback (200 req/day). Stops at the first source that returns data.

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
#   FINNHUB_API_KEY          (free at finnhub.io)
#   NEWSAPI_KEY              (free at newsapi.org, optional)
#   REFINITIV_USERNAME       (LSEG platform — ESG scores)
#   REFINITIV_PASSWORD
#   REFINITIV_APP_KEY
#   ALPHA_VANTAGE_KEY_1..8   (free at alphavantage.co, up to 8 keys)
#   FMP_API_KEY              (free at financialmodelingprep.com)
#   SIMFIN_API_KEY           (free at simfin.com)
# All API keys are optional — the pipeline degrades gracefully
# when keys are missing, skipping the corresponding data sources.
```

**4. Run the pipeline**

```bash
# Full 6-year backfill
poetry run python Main.py --env_type dev

# Daily incremental update
poetry run python Main.py --env_type dev --frequency daily

# Custom date range
poetry run python Main.py --env_type dev --start_date 2023-01-01 --end_date 2024-12-31

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
| `company_ratios` | `(symbol, snapshot_date, field_name)` | 33 financial ratios (EAV) |
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

**756 tests** across 30 test files | **83% coverage**

```
TOTAL                                         3109    242    83%
======================= 756 passed, 5 skipped =========================
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
│   ├── input/                       # 14 downloaders (one per data source)
│   │   ├── base_downloader.py       # Abstract base (circuit breaker, retry)
│   │   ├── price_downloader.py      # Daily OHLCV for 678 equities
│   │   ├── fundamentals_downloader.py  # yfinance quarterly/annual
│   │   ├── edgar_downloader.py      # SEC EDGAR XBRL filings (US)
│   │   ├── finnhub_downloader.py    # Finnhub fundamentals (non-US)
│   │   ├── fmp_downloader.py        # Financial Modeling Prep (non-US supplement)
│   │   ├── simfin_downloader.py     # SimFin bulk financials (non-US supplement)
│   │   ├── alphavantage_downloader.py  # Alpha Vantage (non-US supplement, 8-key fallback)
│   │   ├── fx_downloader.py         # FX rate pairs
│   │   ├── vix_downloader.py        # CBOE Volatility Index
│   │   ├── risk_free_rate_downloader.py
│   │   ├── esg_downloader.py        # ESG sustainability scores (LSEG batch)
│   │   ├── news_downloader.py       # News articles (3-source cascade)
│   │   ├── gdelt_downloader.py      # GDELT DOC API (sentiment gap-fill + backfill)
│   │   ├── newsapi_downloader.py    # NewsAPI (secondary news source)
│   │   └── get_company_static.py    # 678-company universe
│   ├── processing/                  # Data cleaning and transformation
│   │   ├── data_cleaner.py          # Pydantic validation
│   │   ├── sentiment_scorer.py      # VADER + financial domain boost
│   │   └── ticker_utils.py          # Currency mapping, Swiss remap
│   ├── db_ops/                      # Database clients
│   │   ├── sql_conn.py              # PostgreSQL (SQLAlchemy)
│   │   ├── mongo_conn.py            # MongoDB (PyMongo)
│   │   ├── minio_store.py           # MinIO S3-compatible store
│   │   └── kafka_ops.py             # Kafka producer/consumer
│   ├── data_models/                 # Pydantic + SQLAlchemy ORM
│   └── utils/                       # Infrastructure utilities
│       ├── circuit_breaker.py       # Three-state resilience pattern
│       ├── rate_limiter.py          # Token-bucket rate limiting
│       └── retry.py                 # Exponential backoff decorator
├── static/schema/
│   ├── create_tables.sql            # PostgreSQL DDL (12 tables)
│   └── company_static.csv           # Universe of 678 tickers
├── test/                           # 756 tests, 83% coverage
├── docs/                            # Sphinx documentation
└── reports/                         # Security scan results
```

---

## Docker Infrastructure

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres_db` | postgres:16 | 5438 | Primary relational store |
| `mongodb` | mongo:7.0 | 27017 | Document store |
| `minio` | minio/minio | 9000 / 9001 | Object store + console |
| `zookeeper` | confluentinc/cp-zookeeper:7.6.0 | 2181 | Kafka coordination |
| `kafka` | confluentinc/cp-kafka:7.6.0 | 9092 | Event streaming |
| `pgadmin` | dpage/pgadmin4 | 5050 | PostgreSQL GUI |

```bash
docker compose up --build -d    # Start all
docker compose down             # Stop all
docker compose down -v          # Stop and reset data
```

---

<div align="center">

**[MIT License](LICENSE)**

Built with SQLAlchemy, Pydantic, yfinance, confluent-kafka, and Poetry

</div>
