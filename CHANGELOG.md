# Changelog

All notable changes to the Systematic Equity Data Pipeline are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [3.3.0] - 2026-03-20

### Added

- **Comprehensive fundamentals-based ratio fallbacks** — when yfinance
  `Ticker.info` does not return certain fields (common for non-US tickers),
  the pipeline now computes them from real fundamental and price data already
  in the database: `return_on_equity`, `profit_margin`, `operating_margin`,
  `gross_margin`, `pe_ratio_trailing`, `price_to_book`, `book_to_price`,
  `ev_to_ebitda`, `enterprise_value`, `market_cap`, `earnings_growth`,
  `revenue_growth`, `free_cash_flow`, `current_ratio`, and more. All
  computations use only real accounting data from the `fundamentals` table
  and real market prices from `daily_prices` — no fabricated or imputed values.
- **Ratios retry pass** — after the main ratios phase, the pipeline checks
  for tickers that failed due to transient yfinance errors, waits 30 seconds
  for Yahoo rate limits to reset, and re-attempts only the missing tickers.
  This eliminates transient failures on a single fresh run.
- **`earnings_growth` snapshot fallback** — computed from the two most recent
  quarterly EPS values in the fundamentals table when Yahoo doesn't provide it.
- **`revenue_growth` snapshot fallback** — computed from the two most recent
  quarterly revenue values.

### Changed

- **`earnings_stability` threshold** — lowered minimum from 4 to 3 quarterly
  EPS observations and from 3 to 2 growth rates. This captures more tickers
  while still requiring sufficient data for a meaningful standard deviation.
- **Historical ratio growth fields** — changed zero-denominator check from
  `prev != 0` to `abs(prev) > 0.001` to avoid skipping near-zero values
  that would produce valid growth rates.
- **`book_value` derivation** — when `book_value` field is missing from
  fundamentals, uses `stockholders_equity` (same accounting concept).
- **30-second cooldown** before ratios phase to reset yfinance rate limits
  after heavy API usage in prices/fundamentals/ESG/sentiment phases.

---

## [3.2.0] - 2026-03-20

### Removed

- **Finnhub fundamentals phase** — removed from pipeline execution. The Finnhub
  free tier only covers US-listed companies (`/stock/financials-reported` returns
  403 Forbidden for non-US tickers like `.L`, `.PA`, `.DE`, `.TO`, `.SW`). All
  175 non-US tickers were returning SKIPPED (0 successes) while consuming 2+
  minutes per run. EDGAR already provides superior US coverage (430 tickers,
  5+ years of 10-Q/10-K filings).
- **Non-US fundamentals supplement (FMP → SimFin → Alpha Vantage cascade)** —
  removed from pipeline execution. After processing 175 non-US tickers through
  the 3-source cascade, only 1 ticker returned data while consuming 12+ minutes
  per run. These free APIs lack coverage for the London, Euronext, Swiss, and
  Canadian tickers in the investable universe. Non-US quarterly fundamentals
  are adequately covered by Yahoo Finance (~7-11 quarters per ticker).
- **Pipeline runtime reduced** from ~27 minutes to ~13 minutes by eliminating
  the two dead-weight phases (~14 minutes saved, effectively zero data loss).

### Changed

- **Data streams** reduced from 14 to 11 (removed Finnhub, FMP/SimFin/AV
  cascade, and sentiment backfill phases from active execution).
- **Orchestration groups** simplified: Group A.5-A.7 (3 parallel supplement
  threads) replaced with Group A.5 (EDGAR only, sequential after Group A).
- **README.md** updated: data source table, architecture diagram, orchestration
  groups, API keys section, and project structure all reflect the streamlined
  pipeline.
- **Sentiment coverage** — now uses recent articles only (yfinance + NewsAPI +
  GDELT gap-fill). Historical backfill removed; sentiment reflects current
  market conditions rather than stale historical data.
- **`.env.example`** — removed Finnhub, Alpha Vantage, FMP, and SimFin API key
  entries. Only infrastructure credentials, LSEG (ESG), and NewsAPI remain.
- **Sphinx docs** — updated `architecture.rst` and `installation.rst` to remove
  references to disabled data sources.

---

## [3.1.0] - 2026-03-19

### Added

- **GitHub Actions CI/CD pipeline** — automated lint (flake8), format check
  (black, isort), unit tests (pytest with coverage), and security scan (bandit)
  on every push and pull request. Workflow runs on `main` and `feature/**`
  branches. Located at `.github/workflows/ci.yml`.
- **Orchestration sub-package** (`modules/orchestration/`) — Main.py refactored
  from a monolithic 4,187-line file into a clean orchestrator (~700 lines) plus
  8 focused stage modules:
  - `state.py` — shared pipeline state (shutdown flag, inactive tickers) and
    utility functions (date range calculation, DB client factory, health checks,
    log entry creation, inactive ticker detection).
  - `stage_prices.py` — daily OHLCV price data download, cleaning, and upsert.
  - `stage_fundamentals.py` — Yahoo Finance, SEC EDGAR, Finnhub, and non-US
    supplement (FMP/SimFin/Alpha Vantage) fundamentals ingestion.
  - `stage_macro.py` — FX rates, VIX, risk-free rate, and benchmark index
    download and processing.
  - `stage_ratios.py` — company ratio extraction (57 fields), derived ratio
    computation, earnings stability, historical ratio time-series engine.
  - `stage_esg.py` — ESG sustainability scores via LSEG batch API with
    per-ticker yfinance fallback.
  - `stage_sentiment.py` — 3-source news sentiment cascade (yfinance → NewsAPI
    → GDELT) with VADER + financial domain boost scoring, plus GDELT historical
    backfill engine.
  - `__init__.py` — public API re-exports for all stage functions.

- **Dedicated test suites for v3.0.0 downloaders** — comprehensive tests for
  `AlphaVantageFundamentalsDownloader` (79 tests), `FmpFundamentalsDownloader`
  (60 tests), and `SimFinFundamentalsDownloader` (80 tests). Covers ticker
  conversion, key rotation, API response parsing, field mapping, derived field
  computation (EBITDA, FCF, total_debt), deduplication, and error handling.
- **Coverage configuration** (`.coveragerc`) — properly scopes `--cov=modules`
  to exclude orchestration stage files (previously in Main.py, always excluded
  from module coverage). Restores accurate measurement baseline.

### Changed

- **Main.py** — reduced from 4,187 lines to ~700 lines. Now serves as a thin
  orchestrator that imports stage functions from `modules/orchestration/` and
  manages the parallel execution groups (A through E), thread lifecycle, and
  pipeline startup/shutdown flow. All business logic lives in the stage modules.
- **Version alignment** — `pyproject.toml` and `docs/conf.py` now both reflect
  the current version (was stuck at 2.2.0 while CHANGELOG showed 3.0.0).
- **Test directory** kept as `test/` to match the coursework specification
  folder structure diagram. All configs (`pyproject.toml`, `.flake8`, README,
  Sphinx docs) consistently reference `test/`.
- **Test imports** — all 4 affected test files (`test_main_functions.py`,
  `test_main_orchestration.py`, `test_advanced_patterns.py`,
  `test_newsapi_downloader.py`) updated to import from new orchestration
  module locations. Patch targets updated for mock correctness.
- **README.md** — corrected test count (756 → 1,185), coverage (83% → 92%),
  company_ratios description (33 → 57 fields), test output block.

### Fixed

- **ESG `download_batch` session failure** — `_use_lseg` flag now correctly
  set to `False` when LSEG session is dead after 5 retry attempts, preventing
  redundant session calls on subsequent tickers. Fixes the pre-existing
  `test_batch_session_dead_marks_unavailable` test failure.
- **Flake8 violations** — fixed line-too-long (E501) in SQL string literals
  in Main.py and unused import (F401) in stage_ratios.py.

### Infrastructure

- **Project structure** — new `modules/orchestration/` package with 8 files
  adds ~3,400 lines of well-organised, independently testable stage logic.
  Each module has its own imports, docstrings, and clear boundaries.
- **CI/CD** — 3-job GitHub Actions workflow (lint, test, security) ensures
  code quality on every PR. No manual intervention required.

### Test Results (v3.1.0)

- **1,221 tests passing** (0 failures)
- **94% module coverage** (3,995 statements, 256 missed)
- **0 flake8 violations** across Main.py and all modules

---

## [3.0.0] - 2026-03-17

### Added

- **Historical ratios computation engine** — derives 20+ time-series financial
  ratios (P/E, D/E, ROE, margins, EV/EBITDA, FCF yield, growth rates) from
  the `fundamentals` + `daily_prices` tables. Produces 6-year ratio history
  that cannot be obtained from yfinance snapshot data. Runs as Group D after
  snapshot ratios, with 8 parallel workers (DB-only, no API calls).
  New fields: `pe_ratio_hist`, `debt_to_equity_hist`, `roe_hist`,
  `profit_margin_hist`, `gross_margin_hist`, `operating_margin_hist`,
  `ev_to_ebitda_hist`, `roa_hist`, `assets_to_liab_hist`, `fcf_yield_hist`,
  `earnings_to_price_hist`, `book_to_price_hist`, `price_to_book_hist`,
  `debt_to_assets_hist`, `equity_ratio_hist`, `fcf_margin_hist`,
  `ocf_to_debt_hist`, `cashflow_to_price_hist`, `revenue_to_mcap_hist`,
  `ebitda_margin_hist`, `earnings_growth_hist`, `revenue_growth_hist`,
  `net_income_growth_hist`, `ocf_growth_hist`.
- **GDELT historical sentiment backfill** — queries GDELT DOC 2.0 API
  quarter-by-quarter from 2020 to present for tickers with fewer than 4
  years of sentiment coverage. Uses company names (not ticker symbols) for
  higher article match rates. Falls back to neutral sentiment (50.0) when
  no articles found. 12 parallel workers.
- **Non-US fundamentals supplement** — 3-source cascade for international
  tickers: FMP → SimFin → Alpha Vantage. Skips tickers already having 20+
  distinct quarterly report dates. Alpha Vantage uses fallback key ordering
  (up to 9 keys, each used until rate-limited, then next key takes over).
- **Alpha Vantage downloader** (`modules/input/alphavantage_downloader.py`)
  — multi-key fallback system with thread-safe key management.
- **FMP downloader** (`modules/input/fmp_downloader.py`) — Financial
  Modeling Prep fundamentals (note: v3 API deprecated Aug 2025).
- **SimFin downloader** (`modules/input/simfin_downloader.py`) — SimFin
  bulk financial statement data.
- **Fundamentals post-processing derivation** — automatically fills
  `book_value` from `stockholders_equity` and `book_value_per_share` from
  `equity / shares_outstanding` after all fundamentals sources complete.
- **Dividend yield default** — non-dividend payers now get `dividend_yield = 0.0`
  instead of NULL (factually correct).
- **Orphan price purge** — `purge_orphan_prices()` deletes `daily_prices`
  rows for symbols not in `company_static` at pipeline startup.
- **LSEG session management** — `ld.close_session()` called after ESG phase;
  session open and batch call retry up to 5 times with exponential backoff.

### Changed

- **`_detect_inactive_tickers` Signal 2** — now only checks `prices` source
  with `FAILED` status (was checking all sources with `SKIPPED + FAILED`,
  causing a negative feedback loop that degraded coverage each run).
- **Signal 3 (ratio gaps) removed** — missing ratios does not indicate delisting.
- **company_ratios** — expanded from 33 to 57 fields with historical time-series.
  Total rows: 128k → 408k+. Entity coverage: 637/678 (94%).
- **news_sentiment** — expanded from 2026-only to 2020-2026 via GDELT backfill.
  Entity coverage: 674/678 (99.4%).
- **ESG `signon_control`** — kept at `True` with 5-retry session open +
  5-retry batch call for reliable LSEG connectivity. ESG time: 33 min → 41 sec.
- **fundamentals_workers** — increased from 2 to 4 in `conf.yaml`.
- **README.md** — updated to reflect 14 data streams, 57 ratio fields,
  corrected test count (756), coverage (83%).

### Fixed

- **Non-US quarterly fundamentals depth** — `well_covered` check now counts
  `COUNT(DISTINCT report_date)` instead of `COUNT(*)`. Previously, 15 quarters
  × 12 fields = 180 records passed the >= 20 threshold despite having only
  3.7 years of data. All 171 non-US tickers were incorrectly skipped.
- **Sentiment backfill threshold** — now checks distinct YEARS of coverage,
  not total record count. Tickers with 6 records all from 2026 were incorrectly
  marked as well-covered.
- **book_value** — starts from 2020 (was 2021) via derivation from
  `stockholders_equity`.
- **book_value_per_share** — now 6-year coverage (was 3-week snapshot only)
  via derivation from `equity / shares_outstanding`.

### Coverage (Run — 2026-03-17, ~26 min wall-clock)

| Source | Records | Coverage |
|---|---|---|
| `daily_prices` | 1,001,726 (2020 → 2026) | 678 entities |
| `fundamentals` | 218,535 (16 fields, 6 years) | 606 / 678 = **89%** |
| `company_ratios` | 408,158 (57 fields, 6 years) | 637 / 678 = **94%** |
| `news_sentiment` | 4,096 (2020 → 2026) | 674 / 678 = **99.4%** |
| `esg_scores` | 935 | 234 / 678 = **35%** (LSEG ceiling) |
| `fx_rates` | 6,294 | 4 / 4 = **100%** |
| `benchmark_index` | 7,580 | 5 / 5 = **100%** |

---

## [2.2.0] - 2026-03-04

### Added

- **NewsAPI integration** — secondary news source in 3-source cascade
  (yfinance → NewsAPI → GDELT). Triggers only when yfinance returns 0
  articles for a ticker. Runs in parallel across 6 sentiment workers.
  New module: `modules/input/newsapi_downloader.py`. API key loaded from
  `NEWSAPI_KEY` env var (no hardcoded credentials).
- **Computed financial ratios** — 5 derived ratios computed from existing
  Ticker.info fields and stored in `company_ratios` (EAV):
  `book_to_price` (B/P), `earnings_to_price` (E/P),
  `cashflow_to_price` (CF/P), `roe_computed`, `debt_to_equity_inv`.
- **Earnings Stability ratio** — computed from historical quarterly EPS
  in `fundamentals` table: `1 / std_dev(QoQ EPS growth)` over last 12
  quarters. Requires ≥4 quarters of data; capped at 100.0.
- **Shared delisted ticker cache** — thread-safe `_DELISTED_TICKERS` set
  shared across all pipeline phases. When prices or ratios detect a
  delisted/missing ticker (YFTzMissingError, 404), it is cached so ESG,
  sentiment, fundamentals, and ratios skip it immediately without wasting
  API calls.
- **Company ratios field breakdown** in terminal output — Rich table
  showing per-field record counts, symbol coverage, and visual bar
  charts (same style as fundamentals breakdown).
- **34 new tests** covering NewsAPI downloader, article parsing, computed
  ratios (B/P, E/P, CF/P, ROE, D/E), and delisted cache. Total: 790+.

### Changed

- Sentiment pipeline log message updated to show 3-source cascade.
- `.flake8` per-file-ignores expanded: Main.py E402, test/ F401+F841.

---

## [2.1.0] - 2026-03-03

### Added

- **40 new sentiment tests** (`test_news_sentiment.py` rewritten) — covers VADER
  + financial domain boost lexicon, `score_articles`, `aggregate_sentiment`,
  `deduplicate_articles`, composite 0-100 score, and new schema columns
  (`positive_ratio`, `sentiment_score`, `score_dispersion`). Replaces old
  keyword-based tests that referenced the removed `score_headline`/`POSITIVE_KEYWORDS`
  API. Total test count: 706 → 761. Coverage: 79% → 83%.

### Fixed

- **`deduplicate_articles` logger format** — changed `pipeline_logger.debug(msg, arg)`
  to f-string `pipeline_logger.debug(f"... {removed} ...")` to match the pipeline logger
  signature (only accepts single positional argument).
- **Finnhub fundamentals 0% coverage (root cause)** — `futures_wait` timeout
  for Finnhub was 120 s, but processing 175 non-US tickers × 2 frequencies at
  the free-tier rate limit (60 req/min ≈ 1 req/s) requires ≈ 385 s serially
  (≈ 130 s with 3 workers). All 175 tickers were killed mid-flight. Timeout
  raised from 120 s → 500 s; outer `supplement_threads.join(600 s)` remains
  the hard cap.
- **Ratios 183 failures in full pipeline runs (root cause)** — `group_independent_threads`
  (ESG + Sentiment, both using `yfinance.Ticker().info`) were still running
  concurrently with the Group C ratios phase (8 workers × `Ticker.info`),
  saturating Yahoo Finance's undocumented rate limit. Moved the independent-
  threads join to *before* Group C so ESG + Sentiment finish before ratios
  starts. Ratios failures in full runs: 183 → 0.
- **`test_default_frequency` assertion** updated from `'daily'` to `None` to
  match the corrected `--frequency` default (changed in v2.0.0, test not updated).
- **Sphinx docs `release` version** bumped from `1.0.0` → `2.1.0` to match
  pipeline version.
- **Docs `--frequency` default** corrected from `'daily'` to `None (6-year backfill)`
  in `docs/usage.rst`.

---

## [2.0.0] - 2026-03-03

### Added

- **Advanced VADER sentiment scoring engine** (`modules/processing/sentiment_scorer.py`
  rewritten) — dual-layer NLP pipeline replacing the prior keyword scorer:
  - Layer 1: VADER (Valence Aware Dictionary and sEntiment Reasoner) outputs
    compound score in [-1.0, +1.0], handling negation, punctuation, and
    capitalisation per Hutto & Gilbert (2014).
  - Layer 2: Financial domain boost lexicon — 35+ signed single-word entries
    (`beat` +0.22, `miss` −0.22, `bankruptcy` −0.32, etc.) and 20+ multi-word
    phrase entries (`beat estimates` +0.24, `chapter 11` −0.35, etc.) applied
    as an additive correction: `enhanced = clip(vader_raw + 0.35 × boost)`.
  - Article deduplication before scoring — syndicated headlines are
    deduplicated by normalised title to prevent single-story bias.
  - Composite investable score (0–100):
    `sentiment_score = vader_component × 0.45 + positive_ratio × 0.25
    + volume_component × 0.20 + agreement_bonus × 0.10`
  - `score_dispersion` (std dev of per-article VADER scores) stored as a
    standalone factor measuring market disagreement.
- **Three new sentiment DB columns** — `positive_ratio`, `sentiment_score`,
  `score_dispersion` added to `news_sentiment` PostgreSQL table and ORM model.
  Migration applied via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
- **Kafka fire-and-forget across all sources** — all eight remaining
  synchronous `kafka_producer.publish_batch()` / `producer.flush(timeout=10)`
  calls (EDGAR, fundamentals, Finnhub, FX, VIX, RFR, benchmark, ESG) wrapped
  in daemon threads (`threading.Thread(daemon=True).start()`), eliminating the
  10 s/call Kafka ACK stalls that exhausted the PostgreSQL connection pool.
- **download_batch daemon-thread timeout** — `PriceDownloader.download_batch()`
  is now called inside a daemon thread with `join(timeout=90)`. Prevents a
  stuck HTTP socket (which `yf.download(timeout=30)` does not always prevent
  at the libcurl layer) from blocking the prices phase indefinitely.
- **Hard-cap timeouts on all three `t.join()` loops** in `main()`:
  - `group_a_threads` (prices + fundamentals): `join(timeout=2400)` — 40 min
  - `supplement_threads` (EDGAR + Finnhub): `join(timeout=600)` — 10 min
  - `group_independent_threads` (FX + RFR + ESG + Sentiment): `join(timeout=600)`
  Previously all three loops were unbounded; a single hung thread silently
  prevented the rest of the pipeline from running.
- **Company ratios three-source waterfall** — `_run_ratios._process_ticker`
  upgraded from single-source (yfinance `Ticker.info`) to:
  1. `yfinance.Ticker().info` (primary, all tickers)
  2. Finnhub `/stock/metric` endpoint (US-only gap-fill; 22 ratio fields)
  3. `yfinance fast_info` (lightweight fallback for all tickers)
  Coverage: 63.3% → **93.8%** (636 / 678 symbols).
- **`FINNHUB_METRIC_FIELDS`** constant mapping 22 Finnhub `/stock/metric` keys
  to canonical `company_ratios` field names.
- **`_fetch_finnhub_metric_ratios()`** and **`_extract_ratios_from_fast_info()`**
  helper functions in `_run_ratios`.
- **EDGAR / Finnhub / Ratios executor timeout fix** — replaced blocking
  `with ThreadPoolExecutor as executor: list(executor.map(...))` with
  `futures_wait(timeout=N) + pool.shutdown(wait=False)` in all three phases,
  allowing the pipeline to proceed when individual HTTP requests stall.

### Changed

- **`--frequency` default changed from `'daily'` to `None`** — without an
  explicit `--frequency` flag the pipeline now defaults to a full 6-year
  backfill (`lookback_years: 6` from `conf.yaml`, i.e. 2,190 days).
  Use `--frequency daily` for incremental runs (5-day window).
- `upsert_news_sentiment()` in `sql_conn.py` updated to include the three
  new sentiment columns in the `ON CONFLICT DO UPDATE` clause.
- `NewsSentiment` SQLAlchemy model updated with the three new columns.
- `create_tables.sql` `news_sentiment` DDL updated with three new columns.

### Fixed

- **Prices phase hang (root cause 3)** — `download_batch()` had no outer
  timeout; a single batch with a stuck socket blocked the prices thread
  indefinitely despite `yf.download(timeout=30)`, because libcurl (used by
  yfinance's curl_cffi backend) can bypass Python socket timeouts.
- **EDGAR never executing** — `group_a_threads.join()` had no timeout;
  if the prices thread hung, EDGAR (which only starts after that join
  completes) never ran.
- **Pipeline summary never printing** — `group_independent_threads.join()`
  had no timeout; a hung ESG or sentiment thread silently blocked the
  post-pipeline summary indefinitely.
- **vaderSentiment import guard** — graceful fallback (`VADER_AVAILABLE = False`,
  zero-scores returned) when `vaderSentiment` is not installed; warning logged
  with install instruction.

### Coverage (Run 14 — 2026-03-03, ~18 min wall-clock)

| Source | Records | Coverage |
|---|---|---|
| `daily_prices` | 994,084 total (2020-02-27 → 2026-03-02) | 672 / 678 = **99.1%** |
| `fundamentals` | 204,585 total | 606 / 678 = **89.4%** |
| `fundamentals` (EDGAR) | 136,486 records | 436 US symbols |
| `fx_rates` | 6,252 total | 4 / 4 = **100%** |
| `vix_data` | 1,510 rows | 2020 → 2026 |
| `risk_free_rate` | 1,567 rows | 2020 → 2026 |
| `benchmark_index` | 15 rows (latest run) | 5 / 5 indices |
| `company_ratios` | 73,488 total | 636 / 678 = **93.8%** |
| `esg_scores` | 234 records | 34.5% (LSEG ceiling) |
| `news_sentiment` | 1,876 total (VADER scored) | 621 / 678 = **91.6%** |

---

## [1.9.0] - 2026-02-28

### Added

- **EDGAR + Finnhub → MongoDB + Kafka integration** — both supplementary
  fundamental data sources now store rich semi-structured documents in MongoDB
  (`raw_fundamentals` collection) and publish events to the Kafka
  `market.fundamentals` topic. Previously these only wrote to PostgreSQL.
- **EDGAR + Finnhub parallel execution** — US (EDGAR) and non-US (Finnhub)
  supplementary fundamentals now run concurrently in separate threads
  (Group A.5+A.6), reducing total pipeline runtime since they hit different
  external APIs and operate on disjoint ticker sets.
- **Enriched MongoDB documents across all sources** — every data source now
  stores rich semi-structured documents with date ranges, summary statistics,
  company metadata, run IDs, and field-level detail (previously only sparse
  metadata was stored).
- **ESG, news sentiment, and ingestion_log** added to the post-pipeline data
  verification table — the Rich terminal summary now shows row counts, entity
  counts, date ranges, and field completeness for all 10 tables (up from 8).
- **EBITDA 3-layer fallback** in `data_cleaner.py`:
  1. Direct EBITDA aliases (including `ReconciledEBITDA`)
  2. Computed: operating_income + abs(depreciation) via `_compute_ebitda_fallback()`
  3. `ticker.info` TTM value as final fallback
- **Comprehensive ticker.info TTM extraction** — now extracts 15 fundamental
  fields from `yfinance.Ticker().info` as trailing-twelve-month gap fillers
  (was only bookValue + ebitda before).
- **Computed free_cash_flow** post-processing — all three sources (yfinance,
  EDGAR, Finnhub) now compute `free_cash_flow = OCF - abs(capex)` when the
  direct value is missing.
- **Expanded EDGAR XBRL field coverage** — added 8 new XBRL concepts to
  `XBRL_FIELD_MAP` and `XBRL_DEPRECIATION_CONCEPTS` for EBITDA computation.
- **Finnhub depreciation extraction** — added `_depreciation` mapping to
  `CASHFLOW_FIELD_MAP` for computed EBITDA and free_cash_flow.
- **15 new tests** for EBITDA fallback, ticker.info TTM, computed fields
  across all three data sources (EDGAR, Finnhub, yfinance).

### Changed

- `_run_edgar_fundamentals()` and `_run_finnhub_fundamentals()` now accept
  `kafka_producer` and `mongo_store` parameters
- All MongoDB documents include `run_id` for traceability, date ranges,
  summary statistics, and field-level metadata
- Data verification table widened from 8 to 10 tables (added esg_scores,
  news_sentiment, ingestion_log)

---

## [1.8.0] - 2026-02-28

### Added

- **News sentiment pipeline** — full end-to-end data flow:
  - `modules/input/news_downloader.py`: downloads news articles via `yfinance.Ticker().news`
  - `modules/processing/sentiment_scorer.py`: lightweight keyword-based financial sentiment scorer
    (50+ positive, 50+ negative financial domain keywords, normalised score in [-1.0, +1.0])
  - PostgreSQL `news_sentiment` table for aggregated per-ticker daily sentiment scores
  - MongoDB `news_sentiment` collection for raw semi-structured article storage
  - Kafka `market.sentiment` topic for event streaming
  - MinIO backup of raw news JSON
  - `--sources sentiment` CLI flag for selective execution
- **Regional benchmark indices** — expanded from S&P 500 only to 5 regional benchmarks:
  - `^GSPC` (S&P 500, US), `^FTSE` (FTSE 100, UK), `^STOXX50E` (Euro Stoxx 50, EU),
    `^GSPTSE` (S&P/TSX Composite, Canada), `^SSMI` (SMI, Switzerland)
- **Expanded company ratios** — 7 additional fields from `yfinance.Ticker().info`:
  `sharesOutstanding`, `floatShares`, `bookValue` (per share), `freeCashflow`,
  `operatingCashflow`, `totalRevenue` (TTM), `grossMargins`
- **40 new tests** in `test/test_news_sentiment.py` (downloader, parser, scorer,
  aggregator, args, Kafka topic, table model)
- SQL schema `news_sentiment` table with indexes in `create_tables.sql`
- `NewsSentiment` SQLAlchemy model in `table_models.py`
- `upsert_news_sentiment()` method in `sql_conn.py`

### Changed

- Default `--sources` now includes `sentiment`
- `BENCHMARK_SYMBOLS` expanded from 1 to 5 indices
- `RATIO_FIELDS` expanded from 18 to 25 fields
- `TOPICS` in `kafka_ops.py` now includes `sentiment: market.sentiment`
- SQL `init_schema()` now correctly skips comment-only blocks

### Fixed

- `init_schema()` in `sql_conn.py` crashed on comment-only SQL blocks after
  semicolon splitting — now filters out non-executable comment blocks

---

## [1.7.0] - 2026-02-27

### Added

- **Full Kafka integration across all data sources** — every `_run_*` function
  in Main.py now publishes events to the appropriate Kafka topic after
  successful DB upsert: `market.prices`, `market.fundamentals`, `market.fx`,
  `market.macro` (VIX, risk-free rate, benchmark). Previously Kafka was only
  used for ESG events.
- **Full MongoDB integration across all data sources** — every `_run_*`
  function now stores raw API response metadata in MongoDB collections:
  `raw_prices`, `raw_fundamentals`, `raw_fx`, `raw_macro`, `raw_benchmark`,
  `raw_ratios`. Previously MongoDB was only used for ESG reports.
- **APScheduler `--schedule` CLI flag** — `Main.py --schedule` launches a
  recurring pipeline execution using APScheduler's cron triggers, matching
  the `--frequency` argument (daily/weekly/monthly/quarterly). The scheduler
  was previously implemented but never wired into Main.py.
- **MongoDB health check** — `PipelineHealthChecker.check_mongodb()` pings
  the MongoDB client during pre-flight checks.
- **Kafka health check** — `PipelineHealthChecker.check_kafka()` verifies
  the Kafka producer is connectable during pre-flight checks.
- **8 new unit tests** — MongoDB/Kafka health checks (6 tests), `--schedule`
  flag parsing (2 tests). Total: 679+ tests, 87% coverage.

### Changed

- `_run_health_checks()` now passes `mongo_store` and `kafka_producer` to
  `PipelineHealthChecker` so all 7 dependency checks run at startup.
- All `_run_*` functions accept optional `kafka_producer` and `mongo_store`
  keyword arguments for event publishing and raw document storage.

---

## [1.6.0] - 2026-02-27

### Added

- **MongoDB document store** (`modules/db_ops/mongo_conn.py`) — `MongoDBStore`
  class for semi-structured data storage as specified in the assignment:
  "Two database systems are provided by default MongoDB & PostgreSQL."
  Lazy-initialised PyMongo client with graceful degradation when
  `pymongo` is not installed. Collections: `raw_api_responses`,
  `esg_reports`, `news_sentiment`. Methods: `store_document`,
  `store_documents`, `find_documents`, `close`.
- **Apache Kafka event streaming** (`modules/db_ops/kafka_ops.py`) —
  `KafkaProducerClient` and `KafkaConsumerClient` for decoupled
  ingestion as specified: "To handle data ingestion and processing,
  you can leverage on Apache Kafka." Five topics: `market.prices`,
  `market.fundamentals`, `market.fx`, `market.macro`, `esg.scores`.
  JSON-serialised messages keyed by ticker symbol for partition affinity.
- **ESG sustainability scores** (`modules/input/esg_downloader.py`) —
  `EsgDownloader` extending `BaseDownloader` for downloading ESG
  scores from `yfinance.Ticker().sustainability`. Extracts
  `totalEsg`, `environmentScore`, `socialScore`, `governanceScore`,
  `percentile`, and `peerGroup`. Includes `clean_esg_record()`
  function for PostgreSQL-ready validation.
- **APScheduler pipeline scheduling** (`modules/utils/scheduler.py`) —
  `PipelineScheduler` wrapping APScheduler's `BackgroundScheduler`
  for cron-based pipeline execution. Supports daily (18:00 UTC,
  Mon–Fri), weekly (Friday 18:00), monthly (1st day), and quarterly
  (Jan/Apr/Jul/Oct) frequencies.
- **`esg_scores` PostgreSQL table** — new table in `create_tables.sql`
  with columns: `symbol`, `cob_date`, `total_esg`, `environment_score`,
  `social_score`, `governance_score`, `peer_percentile`. Composite
  PK `(symbol, cob_date)` with upsert support.
- **MongoDB Docker service** — `mongo:7.0` container with health check
  (`mongosh --eval 'db.runCommand("ping")'`), persistent volume
  (`mongodata`), and `mongo-seed` init service creating collections
  with indexes.
- **Kafka + Zookeeper Docker services** — Confluent Platform 7.6.0
  images (`cp-zookeeper`, `cp-kafka`) with PLAINTEXT listeners,
  persistent volumes (`zkdata`, `kafkadata`).
- **Bandit security scan report** — saved to `reports/bandit_security_report.txt`.
  Results: 0 high, 3 medium (B310 urllib.urlopen in EDGAR/Finnhub
  downloaders — controlled API URLs), 7 low (B110 try-except-pass,
  B311 random — intentional patterns).
- **`.env.example`** — template file with all environment variables
  documented (no secrets).
- **New test modules** — 4 new test files with 60 test methods:
  - `test/test_mongo_conn.py` — 12 tests (MongoDB CRUD, lazy init,
    graceful degradation).
  - `test/test_kafka_ops.py` — 17 tests (producer, consumer, topics,
    batch publish).
  - `test/test_esg_downloader.py` — 13 tests (sustainability parsing,
    cleaning, retry).
  - `test/test_scheduler.py` — 18 tests (cron definitions, scheduling,
    start/stop).

### Changed

- **`docker-compose.yml`** — expanded from 4 to 8 services; added
  MongoDB, Zookeeper, Kafka, and mongo-seed. Total volumes: 6
  (pgdata, miniodata, mongodata, zkdata, kafkadata + MinIO data).
- **`Main.py`** — integrated MongoDB init (step 11b), Kafka init
  (step 11c), and ESG download phase (Group D after ratios). ESG
  data flows through: download → MongoDB store → Kafka publish →
  clean → PostgreSQL upsert.
- **`config/conf.yaml`** — added MongoDB config (Host, Port, Database,
  Username, Password) and Kafka config (BootstrapServers) for both
  dev and docker profiles. Added `esg` data source entry.
- **`pyproject.toml`** — added `pymongo ^4.6.0`, `confluent-kafka ^2.3.0`,
  `apscheduler ^3.10.0` dependencies.
- **`requirements.txt`** — added `pymongo>=4.6.0`, `confluent-kafka>=2.3.0`,
  `apscheduler>=3.10.0`.
- **`modules/db_ops/sql_conn.py`** — added `upsert_esg_scores()` method.
- **`modules/data_models/table_models.py`** — added `EsgScores` ORM class.
- **`modules/utils/args_parser.py`** — added `esg` to `--sources`
  choices and defaults.
- **`static/schema/create_tables.sql`** — added `esg_scores` table;
  updated header comment to reflect 11 tables.
- **`.env.dev`** — added MongoDB, Kafka, and Finnhub API key variables.
- **`.flake8`** — removed `E501` from `extend-ignore` (was contradicting
  `max-line-length = 110`).
- **Test count** — expanded from 611 to **671+** across 25 test files.

---

## [1.5.0] - 2026-02-27

### Added

- **6-year default lookback** — `lookback_years` increased from 5 to 6 in
  `config/conf.yaml` for both dev and docker environments.
- **SEC EDGAR 10-K annual filings** — `extract_edgar_fundamentals()` now
  extracts both 10-Q quarterly and 10-K annual filings via a new
  `period_types` parameter (default: `('quarterly', 'annual')`). This
  extends US annual fundamentals coverage from ~4.2yr (Yahoo only) to
  5.3yr average.
- **Finnhub fundamentals downloader** — new `FinnhubFundamentalsDownloader`
  in `modules/input/finnhub_downloader.py` for non-US tickers (.L, .PA,
  .DE, .MI, .AS, .TO, .SW). Uses Finnhub's free API (60 req/min).
  Requires `FINNHUB_API_KEY` environment variable.
- **Risk-free rate source** — `RiskFreeRateDownloader` downloads US 3-Month
  Treasury Bill rate (DGS3MO) from FRED public CSV endpoint. Stored in
  `risk_free_rate` table with 6 years of daily data.
- **Benchmark index source** — downloads S&P 500 (^GSPC) daily OHLCV
  into `benchmark_index` table. Full 6 years coverage.
- **Company ratios source** — `RatiosDownloader` extracts 20 financial
  ratios per ticker (P/E, P/B, ROE, ROA, margins, growth, etc.) from
  `yfinance.Ticker().info` into `company_ratios` table.
- **Orchestration groups A.5, A.6, B.1/B.2/B.3, C** — restructured
  pipeline execution to safely handle yfinance thread-safety limitations.

### Fixed

- **yfinance MultiIndex column detection** — `_flatten_columns()` now
  detects which MultiIndex level contains OHLCV field names instead of
  always using level -1. Fixes NULL close prices for single-ticker
  downloads in yfinance 2.x.
- **yfinance thread-safety** — restructured Group B sources to run
  sequentially for yfinance-based single-ticker downloads (VIX, benchmark).
  FX pairs also download sequentially within `download_all()`. This
  prevents response contamination across concurrent threads.
- **FX data contamination** — removed 4 contaminated FX rows where
  close_rate contained S&P 500 values due to thread-safety issue.

### Investigated

- **Alpha Vantage API** — evaluated as a potential source for non-US
  fundamentals. Finding: Alpha Vantage fundamental data endpoints
  (`INCOME_STATEMENT`, `BALANCE_SHEET`, `CASH_FLOW`) are sourced from
  SEC filings and return empty `{}` for all non-US exchange tickers.
  Combined with Finnhub free tier returning 403 for non-US tickers,
  Yahoo Finance remains the only viable free source for international
  fundamental data (~1.6yr quarterly, ~3.4yr annual).

### Changed

- FX downloader `download_all()` changed from parallel to sequential
  to avoid yfinance thread-safety issues.
- EDGAR extraction default now includes both 10-Q and 10-K filings.
- Test suite expanded from 481 to **611 tests** across 21 files;
  coverage increased from 76% to **91%**. New test modules:
  `test_finnhub_downloader.py` (38 tests), `test_progress_tracker.py`
  (26 tests), `test_sql_conn.py` (28 tests), `test_models.py` (33 tests).

---

## [1.4.0] - 2026-02-26

### Added

- **Three-tier parallelism architecture** — maximises throughput for I/O-bound
  Yahoo Finance API calls using `threading.Thread` and `ThreadPoolExecutor`:
  - **Tier 1 — Source-level parallelism:** Independent data sources run
    concurrently. Group A (prices + fundamentals) and Group B (FX + VIX) each
    launch their members as parallel threads, halving wall-clock time.
  - **Tier 2 — Ticker-level parallelism:** Within fundamentals, tickers
    download concurrently via `ConcurrentDownloadExecutor` (default 4 workers).
    FX pairs download in parallel (4 threads for 4 pairs).
  - **Tier 3 — Post-processing parallelism:** After each price batch download,
    per-ticker MinIO storage + cleaning + DB upsert runs concurrently across
    threads (default 6 workers), overlapping I/O operations.
- **Thread-safe `PipelineMetrics`** — all mutable state (`_timings`, `_counts`)
  protected by `threading.Lock` for safe concurrent access from multiple
  source-processing threads.
- **Parallel FX pair downloads** — `FxDownloader.download_all()` accepts
  `parallel=True` (default) and `max_workers` parameters. Uses
  `ConcurrentDownloadExecutor` for near-linear speedup across 4 pairs.
- **Configurable worker counts** — `fundamentals_workers` and
  `price_post_workers` in `conf.yaml` pipeline params for fine-tuning
  concurrency per environment.

### Changed

- **Main.py orchestration** — replaced sequential source execution with
  grouped parallel threads. Prices + fundamentals run concurrently (Group A),
  then FX + VIX run concurrently (Group B). Thread-safe result collection
  via `threading.Lock` for circuit breakers and downloader lists.
- **`_run_prices()`** — batch post-processing now uses `ThreadPoolExecutor`
  for concurrent per-ticker MinIO + cleaning + DB upsert within each batch.
  Thread-safe `total_loaded` counter via `threading.Lock`.
- **`_run_fundamentals()`** — ticker downloads now use
  `ConcurrentDownloadExecutor` with configurable worker count instead of
  sequential iteration. Thread-safe `total_loaded` counter.
- **`PipelineMetrics`** — `track()` and `record_outcome()` now protected
  by `threading.Lock` for concurrent access from multiple source threads.

---

## [1.3.0] - 2026-02-26

### Added

- **Custom exception hierarchy** (`modules/utils/exceptions.py`) —
  Structured exception taxonomy for precise error handling across the pipeline:
  - `PipelineError` (base) with `message` + `details` dict for structured reporting.
  - `DataSourceError` → `APIConnectionError`, `APIRateLimitError`,
    `DataNotFoundError`, `CircuitBreakerOpenError`.
  - `DataValidationError` → `SchemaValidationError`, `CrossFieldValidationError`.
  - `StorageError` → `DatabaseConnectionError`, `DatabaseWriteError`, `ObjectStoreError`.
  - `ConfigurationError` for missing/invalid config.
  - Enables targeted `except` clauses at each pipeline layer.
- **Abstract base downloader** (`modules/input/base_downloader.py`) —
  Template Method design pattern (Gamma et al., 1994) for all Yahoo Finance
  downloaders. Provides shared infrastructure for circuit breaker, rate limiter,
  retry logic, and download statistics. Concrete subclasses override
  `_execute_download()` while inheriting all resilience infrastructure.
  Includes `stats` property for per-downloader success/failure metrics.
- **Retry decorator** (`modules/utils/retry.py`) — Reusable `@retry` decorator
  with configurable backoff strategies:
  - Exponential (`base^attempt`), linear (`base*attempt`), constant.
  - Random jitter to prevent thundering-herd effects (AWS Architecture Blog, 2015).
  - Exception whitelisting — only retry on specified types.
  - Pre-retry callback for structured logging.
  - `max_delay` cap to prevent unbounded waits.
- **Token Bucket rate limiter** (`modules/utils/rate_limiter.py`) —
  Industry-standard algorithm (Turner, 1986) for API request throttling:
  - Configurable rate (requests/second) and burst capacity.
  - Thread-safe with `threading.Lock` for concurrent download support.
  - `acquire()` blocks when bucket is empty, returns wait time.
  - Statistics tracking: `total_waits`, `total_wait_time`, `available_tokens`.
  - Integrated into all four downloaders via `BaseDownloader`.
- **Pre-flight health checks** (`modules/utils/health_check.py`) —
  Fail-fast validation of all external dependencies before downloading:
  - PostgreSQL connectivity (`SELECT 1`).
  - Schema existence (`systematic_equity` tables).
  - MinIO bucket accessibility.
  - Yahoo Finance API reachability (lightweight AAPL probe).
  - Configuration completeness (required keys present).
  - Results displayed in a Rich table with latency measurements.
  - Distinction between critical (PostgreSQL, config) and non-critical (MinIO, Yahoo)
    failures — pipeline aborts only on critical failures.
- **Concurrent download executor** (`modules/utils/concurrent_executor.py`) —
  `ThreadPoolExecutor`-based concurrent execution with:
  - Configurable worker count and per-task timeout.
  - Graceful shutdown support (responds to cancellation signals).
  - Progress callback integration for real-time tracking.
  - Thread-safe result collection.
- **Graceful shutdown** — SIGINT/SIGTERM signal handling in `Main.py`:
  - Sets a global `_shutdown_requested` flag.
  - Checked between pipeline stages — current stage completes, remaining are skipped.
  - Clean resource cleanup (database connections closed).
- **Downloader statistics table** — Rich-formatted table showing per-downloader
  download counts, success/failure rates, and rate limiter wait statistics.
- **Health check table** — Rich-formatted pre-flight health check display
  with service name, status (PASS/FAIL), latency, and details.
- **Comprehensive new test suite** — six new test modules:
  - `test/test_exceptions.py` — 51 tests: hierarchy, messages, details, catching.
  - `test/test_rate_limiter.py` — 19 tests: basic, refill, blocking, stats, edge cases.
  - `test/test_retry_decorator.py` — 23 tests: strategies, callbacks, selective exceptions.
  - `test/test_health_check.py` — 36 tests: config, postgres, schema, minio, yahoo.
  - `test/test_concurrent_executor.py` — 12 tests: execution, callbacks, shutdown.
  - `test/test_base_downloader.py` — 23 tests: init, circuit check, stats, hooks.
- **Total test count: ~370** across 17 test files.

### Changed

- **All four downloaders** now extend `BaseDownloader` (Template Method pattern).
  Each inherits circuit breaker, rate limiter, retry logic, and statistics
  tracking. Custom behaviour is isolated in `_execute_download()`.
- **Downloaders now use `rate_limiter.acquire()`** instead of `time.sleep(api_delay)`
  for intelligent request throttling with burst capacity support.
- **Main.py orchestration** — major enhancements:
  - Signal handlers registered for SIGINT/SIGTERM at startup.
  - Pre-flight health checks run before any downloads.
  - Shutdown flag checked between pipeline stages for graceful termination.
  - Downloader statistics table displayed after all sources complete.
  - Downloader `stats` property logged for each source.
- **`modules/utils/__init__.py`** — exports `TokenBucketRateLimiter` and
  five exception classes (`PipelineError`, `DataSourceError`, `StorageError`,
  `ConfigurationError`, `DataValidationError`).
- **Progress tracker** — added `print_health_checks()` and
  `print_downloader_stats()` methods for new Rich tables.

---

## [1.2.0] - 2026-02-26

### Added

- **Circuit breaker pattern** (`modules/utils/circuit_breaker.py`) —
  Production-grade resilience pattern (Nygard, 2007) for all Yahoo Finance
  downloaders. State machine: CLOSED → OPEN → HALF_OPEN → CLOSED.
  - Opens after configurable consecutive failures (default: 10 for prices,
    5 for FX/VIX), preventing API overload.
  - Recovers automatically after a configurable timeout.
  - Success in HALF_OPEN state closes the circuit (requires 2 consecutive
    successes by default).
  - Circuit state and trip count exported via `to_dict()` for metrics.
- **Animated progress tracking** (`modules/utils/progress_tracker.py`) —
  Rich-based visual pipeline progress using the `rich` library:
  - Animated progress bars with ETA per data source (prices, fundamentals, FX, VIX).
  - Colour-coded outcome indicators: green=SUCCESS, red=FAILED, yellow=SKIPPED.
  - Live circuit breaker status table.
  - Rich summary table at pipeline completion with timing, row counts, success rates.
  - Pipeline startup banner with run ID and universe size.
  - Graceful fallback to plain text logging if `rich` is not installed.
- **`rich` dependency** added to `pyproject.toml` (`^13.7.0`).
- **Advanced test suite** — two new test modules:
  - `test/test_circuit_breaker.py` — 20 tests covering full state machine
    transitions, metrics export, manual reset, and edge cases.
  - `test/test_advanced_patterns.py` — 50+ parametrized tests covering:
    - `@pytest.mark.parametrize` for exhaustive ticker processing.
    - Boundary value analysis for Pydantic models.
    - Error injection (corrupt DataFrames, missing fields, all-NaN data).
    - Progress tracker integration.
    - Frequency lookback parametrized tests.
- **Circuit breaker integration tests** in `test/test_downloaders.py` — 7 new
  tests verifying circuit breaker behaviour within PriceDownloader, FxDownloader,
  and VixDownloader.

### Changed

- **All four downloaders** (PriceDownloader, FundamentalsDownloader, FxDownloader,
  VixDownloader) now accept an optional `circuit_breaker` parameter. Each creates
  a source-specific circuit breaker by default if none is provided.
- **Main.py orchestration** — fully integrated with animated progress tracking:
  - `_run_*` functions accept `progress_update` callback for real-time progress.
  - `main()` creates `PipelineProgressTracker`, displays startup banner,
    wraps each source phase with animated progress bars.
  - Circuit breaker states are displayed after all sources complete.
  - Rich summary table replaces plain text summary.
  - `_run_*` functions return their downloader instance for circuit breaker
    state inspection.
- **`modules/utils/__init__.py`** — exports `CircuitBreaker` and
  `PipelineProgressTracker`.

---

## [1.1.0] - 2026-02-26

### Added

- **Pipeline observability** (`modules/utils/pipeline_metrics.py`) — `PipelineMetrics`
  class with context-manager timing (`metrics.track('prices')`), per-source
  outcome tracking (success/failed/skipped counts), and a structured summary
  report logged at pipeline completion with timing, row counts, and success rates.
- **Data quality layer** (`modules/processing/data_quality.py`) —
  `DataQualityChecker` validates records before DB insertion:
  - Prices: NULL close detection, high/low inversion, negative volume.
  - FX: NULL close rate, non-positive rate detection.
  - Fundamentals: NULL value percentage, field distribution analysis.
  - Fail-open design: issues are logged as warnings, never block ingestion.
- **Cross-field Pydantic validation** — `DailyPrice` model now includes a
  `model_validator` that auto-corrects inverted high/low prices (Yahoo Finance
  occasionally returns these for illiquid securities).
- **Test suite for new modules** (`test/test_pipeline_metrics.py`) — 19 tests
  covering `PipelineMetrics` timing/outcome tracking, `DataQualityChecker`
  for all data types, and cross-field Pydantic model validation.

### Changed

- **Main.py orchestration** — all `_run_*` functions now accept an optional
  `metrics` parameter. `main()` creates a `PipelineMetrics` instance, wraps
  each data source phase in `metrics.track()`, and calls `metrics.log_summary()`
  before exit.
- **MinIO fundamentals storage** — now stores actual raw data (balance sheet,
  income statement, ticker info dicts) instead of just record count metadata.
  This preserves full data lineage in the data lake.
- **`_flatten_columns()`** — uses `get_level_values(-1)` instead of `(0)` to
  correctly extract field names from any MultiIndex configuration.

### Fixed

- **Currency propagation bug** — `clean_fundamentals_data()` accepted a
  `currency` parameter but never passed it to `FundamentalRecord`. All
  fundamentals records were stored with NULL currency. Now correctly
  propagates currency to every balance sheet, income statement, and
  book value per share record.
- **Book value per share date import** — removed unnecessary inner
  `from datetime import date as d` (was shadowing the module-level import).

---

## [1.0.0] - 2026-02-26

### Added

- **Frequency-based lookback** — `--frequency` CLI flag now dynamically sets the
  download window (`daily`=5d, `weekly`=14d, `monthly`=35d, `quarterly`=95d)
  instead of always falling back to `lookback_years`.
- **Security scanning tools** — `bandit` and `safety` added to dev dependencies;
  `[tool.bandit]` section in `pyproject.toml` configures static analysis.
- **Standalone `.flake8` config** — flake8 does not read `pyproject.toml`; a
  dedicated `.flake8` file ensures linting rules are actually applied.
- **Full Sphinx documentation suite** — `docs/index.rst`, `installation.rst`,
  `usage.rst`, `architecture.rst`, `api.rst`, plus `Makefile` and `make.bat`
  for building HTML docs via `make html`.
- **Comprehensive unit tests** — four new test modules:
  - `test/test_downloaders.py` — 21 tests covering PriceDownloader,
    FundamentalsDownloader, FxDownloader, VixDownloader (retry, backoff,
    batch, edge cases).
  - `test/test_minio_store.py` — 9 tests for MinioStore (lazy init, CSV/JSON
    upload, graceful degradation on connection failure).
  - `test/test_main_functions.py` — 14 tests for `_get_date_range`,
    `_make_log_entry`, and `_get_db_client` helpers.
  - `test/test_e2e.py` — 5 end-to-end pipeline tests (price, Swiss ticker, FX,
    fundamentals, VIX) exercising the full download→clean→validate chain with
    mocked APIs.
- **Test fixtures** — `conftest.py` extended with `sample_conf`,
  `mock_parsed_args`, and financial-data fixtures for balance sheets and
  income statements.
- **`.gitkeep`** — empty marker file in project root per spec directory layout.

### Changed

- **Directory rename** — `properties/` → `config/` to match the spec diagram
  (`config/conf.yaml`). `Main.py` updated to pass
  `config_path='./config/conf.yaml'` to `ReadConfig`.
- **README.md** — all references to `properties/` updated to `config/`.
- **`docs/conf.py`** — added `sys.path` insertion for autodoc, Napoleon
  settings, intersphinx mappings, and `autodoc_default_options`.
- **`pyproject.toml`** — removed dead `[tool.flake8]` section (replaced by
  `.flake8` file); added `bandit`, `safety` to dev deps; added
  `[tool.bandit]` configuration.

### Fixed

- `--frequency` flag was previously cosmetic-only (logged but did not affect
  the download date range). Now it drives `_get_date_range()` lookback logic.
- flake8 rules were silently ignored because they were placed in
  `pyproject.toml`, which flake8 does not read.
