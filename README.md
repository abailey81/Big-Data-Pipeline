<div align="center">

# Systematic Equity Data Pipeline

### Production-Grade ETL for Multi-Factor Quantitative Research

*678 equities &middot; 11 data streams &middot; 8 APIs &middot; triple-database architecture &middot; 877 tests*

<br>

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Kafka](https://img.shields.io/badge/Kafka-3.0-231F20?style=for-the-badge&logo=apachekafka&logoColor=white)](https://kafka.apache.org/)
[![Tests](https://img.shields.io/badge/Tests-877_passed-brightgreen?style=for-the-badge)](tests/)
[![Coverage](https://img.shields.io/badge/Coverage-92%25-brightgreen?style=for-the-badge)](tests/)
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
Prices, fundamentals, EDGAR filings, FX, VIX, risk-free rate, ESG, sentiment, and computed ratios
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
<strong>92% Test Coverage</strong>
<br><br>
877 tests across unit, integration, and end-to-end tiers with Bandit security scanning
<br><br>
</td>
</tr>
</table>

---

## Data Sources

| # | Source | API | Records | Coverage |
|---|--------|-----|---------|----------|
| 1 | Daily prices (OHLCV + adjusted close) | Yahoo Finance | ~994k rows | 672 / 678 symbols |
| 2 | Quarterly / annual fundamentals | Yahoo Finance + SEC EDGAR | ~205k rows | 606 / 678 symbols |
| 3 | EDGAR supplementary fundamentals | SEC EDGAR XBRL | ~137k rows | 436 US symbols |
| 4 | Company financial ratios | Yahoo Finance + Finnhub | ~93k rows | 637 / 678 symbols |
| 5 | FX rates (GBP, EUR, CAD, CHF to USD) | Yahoo Finance | ~6.3k rows | 4 / 4 pairs |
| 6 | CBOE Volatility Index (VIX) | Yahoo Finance | ~1.5k rows | 2020--2026 |
| 7 | US 3-Month Treasury rate (DGS3MO) | FRED | ~1.6k rows | 2020--2026 |
| 8 | Regional benchmark indices (5) | Yahoo Finance | ~8k rows | S&P 500, FTSE 100, Euro Stoxx 50, TSX, SMI |
| 9 | ESG sustainability scores | LSEG / yfinance | 234 rows | ~35% (API ceiling) |
| 10 | News sentiment (VADER + financial boost) | yfinance + NewsAPI + GDELT | ~2k rows | 667 / 678 symbols |
| 11 | Computed ratios (B/P, E/P, CF/P, ROE, D/E) | Derived from sources 2 + 4 | per-ticker | 602 / 678 symbols |

**Date range:** 2020-02-27 to present (6-year backfill by default)

---

## Architecture

```
                              +---------------------------+
                              |      Main.py (ETL)        |
                              |   Parallel orchestration  |
                              +-------------+-------------+
                                            |
            +------------+----------+-------+-------+----------+-----------+
            |            |          |               |          |           |
       +----v----+  +----v----+  +-v--------+  +---v---+  +--v----+  +---v----+
       | Yahoo   |  |  SEC   |  | Finnhub  |  | FRED  |  | LSEG  |  | GDELT  |
       | Finance |  | EDGAR  |  | (non-US) |  | T-Bill|  | (ESG) |  | NewsAPI|
       +---------+  +--------+  +----------+  +-------+  +-------+  +--------+
            |            |          |               |          |           |
            +------------+----------+-------+-------+----------+-----------+
                                            |
                          +-----------------v-----------------+
                          |         Data Cleaning             |
                          |  Pydantic validation + DQ checks  |
                          +-----------------+-----------------+
                                            |
           +--------------------------------+--------------------------------+
           |                                |                                |
    +------v------+                  +------v------+                  +------v------+
    |  PostgreSQL  |                  |   MongoDB   |                  |    MinIO    |
    | 12 tables    |                  | (documents) |                  | (data lake) |
    +------+-------+                  +-------------+                  +-------------+
           |
    +------v------+
    |    Kafka    |
    | (6 topics)  |
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

**877 tests** across 30 test files | **92% coverage**

```
TOTAL                                         3109    242    92%
======================= 877 passed, 5 skipped =========================
```

Three-tier testing strategy:

| Tier | Description | Infrastructure |
|------|-------------|----------------|
| **Unit** | Individual modules with mocked external dependencies | None required |
| **Integration** | PostgreSQL upsert idempotency and schema checks | Docker (auto-skipped if unavailable) |
| **End-to-End** | Full pipeline workflows from CLI to database | Mocked at boundaries |

```bash
# Full test suite
poetry run pytest ./tests/

# Unit tests only
poetry run pytest ./tests/ -m "not integration"

# With HTML coverage report
poetry run pytest ./tests/ --cov-report=html
```

---

## Project Structure

```
Big-Data-Pipeline/
├── Main.py                          # Pipeline entry point and orchestrator
├── pyproject.toml                   # Poetry dependencies and tool config
├── docker-compose.yml               # Infrastructure services (8 containers)
├── config/
│   └── conf.yaml                    # Pipeline configuration (dev + docker)
├── modules/
│   ├── input/                       # 10 downloaders (one per data source)
│   │   ├── base_downloader.py       # Abstract base (circuit breaker, retry)
│   │   ├── price_downloader.py      # Daily OHLCV for 678 equities
│   │   ├── fundamentals_downloader.py
│   │   ├── edgar_downloader.py      # SEC EDGAR XBRL filings
│   │   ├── finnhub_downloader.py    # Non-US fundamentals
│   │   ├── fx_downloader.py         # FX rate pairs
│   │   ├── vix_downloader.py        # CBOE Volatility Index
│   │   ├── risk_free_rate_downloader.py
│   │   ├── esg_downloader.py        # ESG sustainability scores
│   │   ├── ratios_downloader.py     # Financial ratios (3-source)
│   │   ├── news_downloader.py       # News articles (3-source cascade)
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
├── tests/                           # 877 tests, 92% coverage
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
