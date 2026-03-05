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
   |  (Pydantic)  |      |  11 tables    |
   +--------------+      +---------------+

Data Flow
----------

1. **Extract** -- Specialised downloaders fetch data from Yahoo Finance:

   - ``PriceDownloader`` -- daily OHLCV for 678 equities
   - ``FundamentalsDownloader`` -- quarterly balance sheet + income statement
   - ``EdgarDownloader`` -- SEC EDGAR 10-Q/10-K filings (US companies)
   - ``FinnhubDownloader`` -- Finnhub fundamentals (non-US tickers)
   - ``FxDownloader`` -- GBP, EUR, CAD, CHF vs USD
   - ``VixDownloader`` -- CBOE Volatility Index
   - ``EsgDownloader`` -- ESG sustainability scores
   - ``RiskFreeRateDownloader`` -- FRED DGS3MO T-bill rate
   - ``RatiosDownloader`` -- 20 financial ratios per ticker

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
   |   +-- sql_conn.py         DatabaseMethods: upsert for all 11 tables
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
   +-- processing/
   |   +-- ticker_utils.py     Whitespace, currency inference, Swiss remap
   |   +-- data_cleaner.py     Pydantic validation, NaN coercion, EAV transform
   |   +-- data_quality.py     Post-clean quality checks (fail-open)
   +-- output/                 (Reserved for CW2)
   +-- utils/
       +-- args_parser.py      CLI argument definitions
       +-- info_logger.py      IFTLogger + run ID generation
       +-- scheduler.py        APScheduler cron-based pipeline scheduling
       +-- circuit_breaker.py  Circuit breaker state machine
       +-- rate_limiter.py     Token bucket rate limiter
       +-- retry.py            @retry decorator with backoff strategies
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
     - ``(symbol, report_date, field_name)``
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

* **Upsert Safety** -- all tables use ``INSERT ... ON CONFLICT DO UPDATE``
  to guarantee idempotent re-runs.
* **EAV Pattern** -- fundamentals table stores arbitrary financial metrics
  without schema migration.
* **Graceful Degradation** -- MinIO failures are logged but do not halt the pipeline.
* **Pydantic Validation** -- all incoming data passes through typed models
  before database insertion.
* **ift_global Integration** -- leverages ReadConfig, IFTLogger, MinioFileSystemRepo,
  and trim_string from the shared UCL IFT library.
