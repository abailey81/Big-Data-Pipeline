Architecture Overview
======================

System Architecture
--------------------

.. code-block:: text

   Yahoo Finance API        +-------------+
         |                  |   MongoDB   |  (ESG, API caches,
         v                  | (doc store) |   news sentiment)
   +--------------+    +--->+-------------+
   |  Downloaders |----|
   |  (yfinance)  |----|-->+------------+
   +------+-------+    |  |   MinIO    |  (raw CSV/JSON, data lake)
          |             |  |  bucket:   |
          |             |  | iftbigdata |
          |             |  +------------+
          |             |
          |             +-->+-----------+
          |                 |  Kafka    |  (event streaming)
          v                 +-----------+
   +--------------+      +---------------+
   |  Cleaning &  |----->|  PostgreSQL   |  (validated, schema: systematic_equity)
   |  Validation  |      |  db: fift     |
   |  (Pydantic)  |      |  12 tables    |
   +--------------+      +---------------+

Data Flow
----------

1. **Extract** -- Specialised downloaders fetch data from Yahoo Finance:

   - ``PriceDownloader`` -- daily OHLCV for 678 equities
   - ``FundamentalsDownloader`` -- quarterly balance sheet + income statement
   - ``EdgarDownloader`` -- SEC EDGAR 10-Q/10-K filings (US companies)
   - ``FxDownloader`` -- GBP, EUR, CAD, CHF vs USD
   - ``VixDownloader`` -- CBOE Volatility Index
   - ``EsgDownloader`` -- ESG sustainability scores (LSEG batch)
   - ``RiskFreeRateDownloader`` -- FRED DGS3MO T-bill rate
   - ``NewsDownloader`` -- 3-source news cascade (yfinance + NewsAPI + GDELT)
   - ``RatiosDownloader`` -- 57 financial ratios per ticker

2. **Raw Storage** -- ``MinioStore`` persists raw CSV/JSON files in the MinIO
   data lake under ``raw-data/{category}/{symbol}/{date}.csv``.

3. **Document Storage** -- ``MongoDBStore`` stores raw API response metadata
   for all data sources in MongoDB collections: ``raw_prices``,
   ``raw_fundamentals``, ``raw_fx``, ``raw_macro``, ``raw_benchmark``,
   ``raw_ratios``, ``esg_reports``.

4. **Event Streaming** -- ``KafkaProducerClient`` publishes events from all
   data sources to Kafka topics (``market.prices``, ``market.fundamentals``,
   ``market.fx``, ``market.macro``, ``esg.scores``) after successful DB upsert
   for decoupled downstream processing.

5. **Transform** -- ``data_cleaner`` module flattens multi-level columns,
   coerces NaN/inf to None, and validates each record through Pydantic models.

6. **Load** -- ``DatabaseMethods`` performs upsert operations
   (``INSERT ... ON CONFLICT DO UPDATE``) into PostgreSQL.

7. **Audit** -- Every download attempt is logged in ``ingestion_log`` with
   status, row count, error messages, and run metadata.

Module Structure
-----------------

.. code-block:: text

   modules/
   +-- data_models/
   |   +-- models.py           Pydantic validation: DailyPrice, FundamentalRecord, FxRate, VixRecord
   |   +-- table_models.py     SQLAlchemy ORM: CompanyStatic, DailyPrices, EsgScores, ...
   +-- db_ops/
   |   +-- postgres_config.py  Pydantic config with environment variable fallback
   |   +-- sql_conn.py         DatabaseMethods: upsert for all 12 tables
   |   +-- extract_from_query.py  Read wrapper using context-managed connections
   |   +-- minio_store.py      MinioStore: raw data lake operations
   |   +-- mongo_conn.py       MongoDBStore: document store (ESG, API caches)
   |   +-- kafka_ops.py        KafkaProducerClient/KafkaConsumerClient: event streaming
   +-- input/
   |   +-- base_downloader.py  Abstract base (circuit breaker, rate limiter, retry)
   |   +-- get_company_static.py  Read 678-company investable universe
   |   +-- price_downloader.py    OHLCV batch download with retry
   |   +-- fundamentals_downloader.py  Balance sheet + income + info
   |   +-- edgar_downloader.py    SEC EDGAR XBRL fundamentals (US 10-Q/10-K)
   |   +-- finnhub_downloader.py  Finnhub fundamentals (non-US tickers)
   |   +-- fx_downloader.py       FX rate pairs
   |   +-- vix_downloader.py      VIX index
   |   +-- esg_downloader.py      ESG sustainability scores
   |   +-- risk_free_rate_downloader.py  FRED DGS3MO T-bill rate
   |   +-- ratios_downloader.py   Company financial ratios (20 fields)
   |   +-- news_downloader.py    yfinance news articles (primary)
   |   +-- newsapi_downloader.py NewsAPI gap-fill (secondary)
   |   +-- gdelt_downloader.py   GDELT DOC API gap-fill (tertiary)
   +-- processing/
   |   +-- ticker_utils.py     Whitespace, currency inference, Swiss remap
   |   +-- data_cleaner.py     Pydantic validation, NaN coercion, EAV transform
   |   +-- data_quality.py     Post-clean quality checks (fail-open)
   |   +-- sentiment_scorer.py VADER + financial domain boost scoring
   +-- output/                 (Reserved for CW2)
   +-- utils/
       +-- args_parser.py      CLI argument definitions
       +-- info_logger.py      IFTLogger + run ID generation
       +-- exceptions.py       Custom exception hierarchy
       +-- scheduler.py        APScheduler cron-based pipeline scheduling
       +-- circuit_breaker.py  Circuit breaker state machine
       +-- rate_limiter.py     Token bucket rate limiter
       +-- retry.py            @retry decorator with backoff strategies
       +-- concurrent_executor.py  ThreadPoolExecutor wrapper
       +-- health_check.py     Pre-flight dependency checks
       +-- pipeline_metrics.py Timing and metrics (thread-safe)
       +-- progress_tracker.py Rich animated progress bars

Database Schema
----------------

All tables reside in the ``systematic_equity`` schema within the ``fift`` database.

.. list-table::
   :header-rows: 1
   :widths: 25 30 45

   * - Table
     - Primary Key
     - Purpose
   * - ``company_static``
     - ``(symbol)``
     - 678-company investable universe reference data
   * - ``daily_prices``
     - ``(symbol, cob_date)``
     - OHLCV + adjusted close in local currency
   * - ``fundamentals``
     - ``(symbol, report_date, field_name, period_type)``
     - EAV pattern for flexible financial metrics
   * - ``fx_rates``
     - ``(currency_pair, cob_date)``
     - Daily FX rates (GBP, EUR, CAD, CHF vs USD)
   * - ``vix_data``
     - ``(cob_date)``
     - Daily CBOE Volatility Index
   * - ``risk_free_rate``
     - ``(cob_date)``
     - Daily US 3-Month Treasury Bill rate (DGS3MO)
   * - ``benchmark_index``
     - ``(symbol, cob_date)``
     - Daily S&P 500 OHLCV (^GSPC)
   * - ``company_ratios``
     - ``(symbol, snapshot_date, field_name)``
     - Point-in-time financial ratios (20 fields)
   * - ``esg_scores``
     - ``(symbol, cob_date)``
     - ESG sustainability scores (total, E, S, G, percentile)
   * - ``news_sentiment``
     - ``(symbol, cob_date)``
     - VADER composite score + dispersion per ticker
   * - ``ingestion_log``
     - ``(log_id)`` auto-increment
     - Audit trail for every download attempt
   * - ``pipeline_metadata``
     - ``(data_source, symbol)``
     - Tracks last successful run for incremental loading

Data Quality Solutions (Spec 7.2)
----------------------------------

.. list-table::
   :header-rows: 1
   :widths: 8 30 62

   * - Issue
     - Problem
     - Solution
   * - 1
     - Trailing whitespace in ticker symbols
     - ``ift_global.trim_string()`` via ``clean_ticker()``
   * - 2
     - No currency column in company_static
     - ``infer_currency()`` maps exchange suffix to ISO code
   * - 3
     - Swiss .S vs Yahoo Finance .SW
     - ``remap_swiss_ticker()`` converts .S to .SW
   * - 4
     - Delisted/acquired companies return empty data
     - Graceful failure with SKIPPED status in log
   * - 5
     - Yahoo Finance rate limiting
     - Exponential backoff + configurable batch downloads
   * - 6
     - Inconsistent fundamentals naming
     - Robust alias mapping with NULL fallback

Key Design Patterns
---------------------

* **Template Method** (GoF 1994) -- ``BaseDownloader`` defines the download workflow
  (validate → pre-download → execute → post-download), with concrete subclasses
  overriding only ``_execute_download()``. Shared infrastructure (circuit breaker,
  rate limiter, retry) is inherited, not re-implemented.
* **Circuit Breaker** -- Three-state machine (CLOSED → OPEN → HALF_OPEN) prevents
  cascading failures when an API goes down. After N consecutive failures the circuit
  opens and all requests are immediately rejected until the recovery timeout expires.
* **Token Bucket Rate Limiter** -- Controls API request rate with configurable burst
  capacity. Prevents Yahoo Finance / SEC EDGAR rate limit breaches.
* **Upsert Safety** -- all tables use ``INSERT ... ON CONFLICT DO UPDATE``
  to guarantee idempotent re-runs.
* **EAV Pattern** -- fundamentals table stores arbitrary financial metrics
  without schema migration.
* **MapReduce Pattern** -- ``ThreadPoolExecutor`` distributes per-ticker downloads
  across worker threads (map phase), while PostgreSQL ``ON CONFLICT DO UPDATE``
  aggregates results into normalised tables (reduce phase).
* **Graceful Degradation** -- MinIO, MongoDB, and Kafka failures are logged but do
  not halt the pipeline; PostgreSQL is the only hard dependency.
* **Pydantic Validation** -- all incoming data passes through typed models
  before database insertion.
* **ift_global Integration** -- leverages ReadConfig, IFTLogger, MinioFileSystemRepo,
  and trim_string from the shared Kolmogorov's team library.

Testing Strategy
-----------------

The test suite follows a three-tier strategy aligned with the testing pyramid:

**Unit Tests** (877 tests) -- Test individual modules in isolation with all external
dependencies mocked. Each source module has a dedicated test file. No infrastructure
required.

**Integration Tests** (5 tests) -- Test database upsert idempotency and schema
initialisation against a live PostgreSQL instance. Automatically skipped when
PostgreSQL is not available via TCP socket probe.

**End-to-End Tests** -- Test full pipeline workflows from CLI argument parsing
through data cleaning to database writes.

**Coverage:** 92% across 3,109 statements (well above the 80% minimum requirement).

Code Quality Tools
-------------------

.. list-table::
   :header-rows: 1

   * - Tool
     - Purpose
     - Configuration
   * - **Black**
     - Opinionated code formatter
     - ``line-length = 110``, ``target-version = ["py310"]`` (pyproject.toml)
   * - **isort**
     - Import sorting
     - ``profile = "black"``, ``line_length = 110`` (pyproject.toml)
   * - **flake8**
     - PEP 8 linting
     - ``.flake8`` with per-file ignores for Main.py and tests
   * - **Bandit**
     - Security static analysis
     - ``pyproject.toml``: exclude tests, skip B101

All 44 source files pass Black, isort, and flake8 with zero violations.

Security Audit
---------------

**Bandit** (static analysis): 0 high-severity issues across 7,232 lines.
4 medium findings are intentional ``urllib.urlopen`` calls to hardcoded SEC/Finnhub
API endpoints. 6 low findings are ``random.uniform`` for jitter backoff (not
cryptographic) and ``try/except/pass`` in ESG fallback paths (graceful degradation).

**Safety** (dependency scanning): 1 low-severity advisory in an indirect dependency
with no production impact. All direct dependencies are pinned to current stable
versions via Poetry's lock file.

Pipeline Flexibility
---------------------

The pipeline supports multiple run frequencies through the ``--frequency`` CLI
argument, enabling both full historical backfill and incremental daily updates:

.. list-table::
   :header-rows: 1

   * - Frequency
     - Lookback
     - Use Case
   * - *(omitted)*
     - 6 years
     - Initial data seeding (full backfill)
   * - ``daily``
     - 5 business days
     - Nightly incremental update
   * - ``weekly``
     - 14 days
     - Weekly refresh with overlap buffer
   * - ``monthly``
     - 35 days
     - Month-end rebalance processing
   * - ``quarterly``
     - 95 days
     - Quarterly earnings window

Custom date ranges override frequency-based lookback via ``--start_date`` and
``--end_date``. The ``--sources`` flag enables selective execution of individual
phases, and ``--tickers`` restricts to specific symbols. The ``--schedule`` flag
starts an APScheduler cron job for automated recurring runs.

Dependency Management
----------------------

All dependencies are managed via **Poetry** with ``pyproject.toml`` as the single
source of truth. Production dependencies (14 packages) and development dependencies
(8 packages) are separated into distinct groups. The ``poetry.lock`` file ensures
reproducible builds across environments.
