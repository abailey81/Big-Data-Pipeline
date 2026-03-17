"""

Kolmogorov's team
Author  : Kolmogorov's team
Topic   : Main.py
Project : Systematic Equity Pipeline - Data Pipeline for Flow-Based Multi-Factor Equity Strategy

Orchestrates the full ETL pipeline:
  Yahoo Finance → MinIO (raw) → cleaning/validation → PostgreSQL (clean)

Features:
  - Pre-flight health checks for all external dependencies
  - Animated progress tracking with rich progress bars
  - Circuit breaker protection on all Yahoo Finance API calls
  - Token bucket rate limiting to prevent API throttling
  - Data quality validation (fail-open design)
  - Pipeline observability metrics and rich summary tables
  - Graceful shutdown via SIGINT/SIGTERM signal handling

Usage:
  poetry run python Main.py --env_type dev --init_schema
  poetry run python Main.py --env_type dev --frequency daily
  poetry run python Main.py --env_type dev --sources prices vix
  poetry run python Main.py --env_type dev --dry_run

"""

import signal
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: F401
from concurrent.futures import wait as futures_wait

# Apply a global socket timeout to prevent yfinance HTTP requests from
# hanging indefinitely (yfinance has no built-in request timeout).
# 60 s is long enough for legitimate slow responses but prevents
# indefinite hangs that would block pipeline threads permanently.
socket.setdefaulttimeout(60)

from datetime import datetime, timedelta

import pandas as pd
import requests
from ift_global import ReadConfig
from ift_global.utils.set_env_var import set_env_variables

from modules.db_ops.kafka_ops import TOPICS, KafkaProducerClient
from modules.db_ops.minio_store import MinioStore
from modules.db_ops.mongo_conn import MongoDBStore
from modules.db_ops.postgres_config import PostgresConfig
from modules.db_ops.sql_conn import DatabaseMethods
from modules.input.edgar_downloader import (
    EdgarFundamentalsDownloader,
    extract_edgar_fundamentals,
    is_us_ticker,
)
from modules.input.esg_downloader import EsgDownloader, clean_esg_record
from modules.input.finnhub_downloader import (
    FinnhubFundamentalsDownloader,
    extract_finnhub_fundamentals,
    is_non_us_ticker,
)
from modules.input.fundamentals_downloader import FundamentalsDownloader
from modules.input.fx_downloader import FX_PAIRS, FxDownloader
from modules.input.gdelt_downloader import GdeltDownloader, parse_gdelt_articles
from modules.input.get_company_static import get_ticker_list
from modules.input.news_downloader import NewsDownloader, parse_news_articles
from modules.input.newsapi_downloader import NewsApiDownloader, parse_newsapi_articles
from modules.input.price_downloader import PriceDownloader
from modules.input.risk_free_rate_downloader import RiskFreeRateDownloader
from modules.input.vix_downloader import VixDownloader
from modules.input.fmp_downloader import FmpFundamentalsDownloader
from modules.input.simfin_downloader import SimFinFundamentalsDownloader
from modules.input.alphavantage_downloader import AlphaVantageFundamentalsDownloader
from modules.processing.data_cleaner import (
    clean_fundamentals_data,
    clean_fx_dataframe,
    clean_price_dataframe,
    clean_risk_free_rate_dataframe,
    clean_vix_dataframe,
)
from modules.processing.data_quality import DataQualityChecker
from modules.processing.sentiment_scorer import aggregate_sentiment, deduplicate_articles, score_articles
from modules.processing.ticker_utils import prepare_yfinance_ticker
from modules.utils import arg_parse_cmd, generate_run_id, pipeline_logger
from modules.utils.concurrent_executor import ConcurrentDownloadExecutor
from modules.utils.health_check import PipelineHealthChecker
from modules.utils.pipeline_metrics import PipelineMetrics
from modules.utils.progress_tracker import PipelineProgressTracker
from modules.utils.scheduler import PipelineScheduler

# ── Global shutdown flag for graceful termination ──
_shutdown_requested = False

# ── Inactive/delisted tickers detected via database query ──
# Populated once by _detect_inactive_tickers() after prices phase completes.
# Tickers with zero rows in daily_prices are definitively inactive.
_inactive_tickers: set[str] = set()



def _signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for graceful pipeline shutdown.

    Sets a global flag that is checked between processing stages.
    Currently running downloads complete, but no new stages start.

    :param signum: Signal number (2=SIGINT, 15=SIGTERM)
    :param frame: Current stack frame (unused)
    """
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    pipeline_logger.warning(
        f"Received {sig_name} — initiating graceful shutdown. " f"Current stage will complete before exit."
    )
    _shutdown_requested = True


def _check_shutdown(stage: str = "") -> bool:
    """Check if a graceful shutdown has been requested.

    :param stage: Name of the stage about to start (for logging)
    :type stage: str
    :return: True if shutdown was requested
    :rtype: bool
    """
    if _shutdown_requested:
        pipeline_logger.warning(f"Shutdown requested — skipping {stage}" if stage else "Shutdown requested")
        return True
    return False


def _detect_inactive_tickers(db_client, ticker_map=None) -> set[str]:
    """Detect inactive/delisted tickers using multi-signal analysis + live verification.

    This is a dynamic, non-hardcoded, non-cached detection system that runs
    every pipeline invocation.  Three independent signals are combined:

    1. **Stale price signal** — tickers whose most recent price in the DB is
       older than 180 trading days.  Delisted stocks stop receiving new data.
    2. **Ingestion-log signal** — tickers that the prices phase SKIPPED or
       FAILED in the most recent pipeline run (often due to YFTzMissingError).
    3. **Live verification** — each candidate is checked via ``yf.Ticker().fast_info``
       for a non-zero ``regularMarketPrice``.  Only tickers that FAIL this live
       check are confirmed inactive.

    The live step makes this highly accurate: a ticker that was merely suspended
    or had a temporary API glitch will pass the live check and remain active.

    Typical runtime: ~5 s for 50-80 candidates (parallelised with 10 workers).

    :param db_client: PostgreSQL database client with read_query method
    :param ticker_map: Optional list of (db_symbol, yf_ticker, currency) tuples.
        Used to map db_symbol → yf_ticker for live checks.
    :return: Set of db_symbol strings for confirmed inactive tickers
    """
    import yfinance as yf

    candidates: set[str] = set()

    # ── Signal 1: Stale prices (no data in last 180 calendar days) ──
    try:
        stale_rows = db_client.read_query(
            "SELECT cs.symbol, MAX(dp.cob_date) AS last_date "
            "FROM systematic_equity.company_static cs "
            "LEFT JOIN systematic_equity.daily_prices dp "
            "  ON TRIM(cs.symbol) = TRIM(dp.symbol) "
            "GROUP BY cs.symbol "
            "HAVING MAX(dp.cob_date) IS NULL "
            "   OR MAX(dp.cob_date) < CURRENT_DATE - INTERVAL '180 days'"
        )
        stale_symbols = {r[0].strip() for r in stale_rows} if stale_rows else set()
        if stale_symbols:
            pipeline_logger.info(
                f"Signal 1 (stale prices): {len(stale_symbols)} tickers "
                f"with no price data in last 180 days"
            )
        candidates |= stale_symbols
    except Exception as exc:
        pipeline_logger.warning(f"Stale-price signal query failed: {exc}")

    # ── Signal 2: Recent ingestion-log FAILED in prices ──
    # Only prices FAILURES are a reliable indicator of a delisted ticker.
    # SKIPPED means "we deliberately didn't try" (e.g. ticker was already
    # in _inactive_tickers from a previous run) — including SKIPPED creates
    # a feedback loop where skipping compounds across runs.
    # FAILED means "we tried and it broke" — a genuine signal.
    try:
        log_rows = db_client.read_query(
            "SELECT DISTINCT TRIM(symbol) "
            "FROM systematic_equity.ingestion_log "
            "WHERE data_source = 'prices' "
            "  AND status = 'FAILED' "
            "  AND run_timestamp > NOW() - INTERVAL '7 days' "
            "  AND symbol IS NOT NULL"
        )
        log_symbols = {r[0] for r in log_rows} if log_rows else set()
        if log_symbols:
            pipeline_logger.info(
                f"Signal 2 (ingestion log): {len(log_symbols)} tickers " f"SKIPPED/FAILED across recent runs"
            )
        candidates |= log_symbols
    except Exception as exc:
        pipeline_logger.warning(f"Ingestion-log signal query failed: {exc}")

    # Signal 3 (ratio gaps) removed — missing ratio data does not indicate
    # delisting; many international tickers legitimately have no yfinance
    # ratio coverage.  Including this signal inflated the inactive set.

    if not candidates:
        pipeline_logger.info("Pre-flight delisted detection: 0 candidates — all tickers look active")
        return set()

    pipeline_logger.info(
        f"Pre-flight delisted detection: {len(candidates)} candidates identified, "
        f"running live verification..."
    )

    # Build db_symbol → yf_ticker mapping
    sym_to_yf: dict[str, str] = {}
    if ticker_map:
        for db_sym, yf_tick, _cur in ticker_map:
            sym_to_yf[db_sym.strip()] = yf_tick
    else:
        # Fallback: use db_symbol as yfinance ticker
        for s in candidates:
            sym_to_yf[s] = s.replace(".", "-")

    # ── Signal 3: Live verification via yfinance fast_info ──
    confirmed_inactive: set[str] = set()

    def _check_live(db_symbol: str) -> tuple[str, bool]:
        """Return (db_symbol, True if inactive)."""
        yf_ticker = sym_to_yf.get(db_symbol, db_symbol.replace(".", "-"))
        try:
            t = yf.Ticker(yf_ticker)
            fi = t.fast_info
            price = fi.get("regularMarketPrice", None) if fi else None
            if price is None or float(price) <= 0:
                return (db_symbol, True)
            return (db_symbol, False)
        except Exception:
            # Network errors, 404s, YFTzMissingError — treat as inactive
            return (db_symbol, True)

    candidate_list = sorted(candidates & set(sym_to_yf.keys())) if ticker_map else sorted(candidates)

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_check_live, sym): sym for sym in candidate_list}
        for fut in as_completed(futures):
            try:
                sym, is_inactive = fut.result(timeout=30)
                if is_inactive:
                    confirmed_inactive.add(sym)
            except Exception:
                # Timeout or unexpected error — mark as inactive to be safe
                confirmed_inactive.add(futures[fut])

    if confirmed_inactive:
        pipeline_logger.info(
            f"Pre-flight delisted detection complete: {len(confirmed_inactive)}/{len(candidates)} "
            f"confirmed inactive (will skip in fundamentals, ratios, ESG, sentiment)"
        )
        # Log a few examples for audit trail
        examples = sorted(confirmed_inactive)[:10]
        pipeline_logger.info(
            f"  Examples: {', '.join(examples)}{'...' if len(confirmed_inactive) > 10 else ''}"
        )
    else:
        pipeline_logger.info(
            f"Pre-flight delisted detection: 0/{len(candidates)} confirmed inactive — "
            f"all candidates passed live check"
        )

    return confirmed_inactive


def _get_date_range(conf: dict, parsed_args) -> tuple[str, str]:
    """Calculate start and end dates for data download.

    Uses either explicit CLI dates or derives from frequency/lookback_years.
    When a frequency is specified and no explicit start_date is given,
    the lookback is adjusted to match the run cadence:

    - daily:     5 days  (covers a business week)
    - weekly:   14 days
    - monthly:  35 days
    - quarterly: 95 days

    Falls back to the full ``lookback_years`` config for initial/backfill runs.

    :param conf: Configuration dictionary from ReadConfig
    :param parsed_args: Parsed command line arguments
    :return: Tuple of (start_date, end_date) as YYYY-MM-DD strings
    :rtype: tuple[str, str]
    """
    end_date = parsed_args.end_date or parsed_args.date_run
    if parsed_args.start_date:
        start_date = parsed_args.start_date
    else:
        freq = getattr(parsed_args, "frequency", None)
        freq_lookback_days = {
            "daily": 5,
            "weekly": 14,
            "monthly": 35,
            "quarterly": 95,
        }
        if freq and freq in freq_lookback_days:
            days = freq_lookback_days[freq]
        else:
            lookback = conf["params"]["Pipeline"]["lookback_years"]
            days = 365 * lookback
        dt = datetime.strptime(end_date, "%Y-%m-%d")
        start_date = (dt - timedelta(days=days)).strftime("%Y-%m-%d")
    return start_date, end_date


def _get_db_client(conf: dict) -> DatabaseMethods:
    """Create a DatabaseMethods client from configuration.

    Uses PostgresConfig with environment variable fallback,
    following the base repository pattern.

    :param conf: Configuration dictionary
    :return: Initialised DatabaseMethods instance
    :rtype: DatabaseMethods
    """
    pg_conf = conf["config"]["Database"]["Postgres"]
    pg_config = PostgresConfig(
        username=pg_conf.get("Username"),
        password=pg_conf.get("Password"),
        host=pg_conf.get("Host"),
        port=str(pg_conf.get("Port")),
        database=pg_conf.get("Database"),
    )
    return DatabaseMethods(
        "postgres",
        username=pg_config.username,
        password=pg_config.password,
        host=pg_config.host,
        port=pg_config.port,
        database=pg_config.database,
    )


def _make_log_entry(
    run_id: str,
    data_source: str,
    symbol: str,
    status: str,
    rows: int = 0,
    error: str = None,
    frequency: str = None,
    start: str = None,
    end: str = None,
) -> dict:
    """Create a standardised ingestion log entry.

    :return: Log entry dictionary matching the ingestion_log schema
    :rtype: dict
    """
    entry = {
        "run_id": run_id,
        "data_source": data_source,
        "symbol": symbol,
        "status": status,
        "rows_affected": rows,
    }
    if error:
        entry["error_message"] = error[:500]
    if frequency:
        entry["run_frequency"] = frequency
    if start:
        entry["date_range_start"] = start
    if end:
        entry["date_range_end"] = end
    return entry


def _run_health_checks(db_client, minio_store, conf, tracker, mongo_store=None, kafka_producer=None):
    """Execute pre-flight health checks and display results.

    Verifies that PostgreSQL, MinIO, MongoDB, Kafka, Yahoo Finance,
    and configuration are all healthy before the pipeline begins
    downloading data.

    :param db_client: Database client for PostgreSQL checks
    :param minio_store: MinIO store for object storage checks
    :param conf: Pipeline configuration dictionary
    :param tracker: Progress tracker for health check display
    :param mongo_store: MongoDB store for document storage checks
    :param kafka_producer: Kafka producer for event streaming checks
    :return: True if critical services are healthy
    :rtype: bool
    """
    checker = PipelineHealthChecker(
        db_client,
        minio_store,
        mongo_store=mongo_store,
        kafka_producer=kafka_producer,
        conf=conf,
    )
    results = checker.run_all()

    # Display health check results
    tracker.print_health_checks(results)

    if not checker.critical_healthy(results):
        pipeline_logger.error("Critical health checks failed — aborting pipeline")
        for r in results:
            if not r.healthy:
                pipeline_logger.error(f"  FAIL: {r.name} — {r.message}")
        return False

    # Log non-critical warnings
    for r in results:
        if not r.healthy:
            pipeline_logger.warning(
                f"Non-critical health check failed: {r.name} — "
                f"{r.message}. Pipeline will continue with degraded mode."
            )

    return True


def _run_prices(
    db_client,
    minio_store,
    ticker_map,
    pipeline_params,
    start_date,
    end_date,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
    kafka_producer=None,
    mongo_store=None,
):
    """Download, clean, and load daily price data for all tickers.

    Uses batch downloads with rate limiting (Spec §7.2 Issue 5).
    Circuit breaker protection prevents overwhelming degraded API.
    Logs success/failure per ticker (Spec §8.3).
    Runs data quality checks on each batch before insertion.

    After each batch download, per-ticker post-processing (MinIO storage,
    cleaning, validation, DB upsert) runs concurrently across threads for
    I/O-bound parallelism on database and object store operations.
    """
    pipeline_logger.info("Starting price data download...")
    downloader = PriceDownloader(
        api_delay=pipeline_params["api_delay_seconds"],
        max_retries=pipeline_params["max_retries"],
        backoff_base=pipeline_params["backoff_base"],
    )
    dq = DataQualityChecker("prices")
    batch_size = pipeline_params.get("batch_size", 50)
    post_workers = pipeline_params.get("price_post_workers", 6)
    total_loaded = 0
    _total_lock = threading.Lock()

    def _process_ticker(args_tuple):
        """Process one ticker's price data: MinIO + clean + upsert (thread-safe)."""
        nonlocal total_loaded
        db_symbol, yf_ticker, currency, df = args_tuple

        if df is not None and not df.empty:
            # ── PRIMARY: clean + upsert to PostgreSQL FIRST ──
            # MinIO and MongoDB are backup stores; do PostgreSQL first to
            # guarantee data is saved even when backup stores are slow.
            records = clean_price_dataframe(df, db_symbol, currency)
            dq.log_report(dq.check_price_records(records), db_symbol)

            # ── BACKUP: MinIO raw CSV — fire-and-forget daemon thread ──
            # Do NOT join/wait: MinIO can stall indefinitely on this host.
            try:
                _csv_bytes = df.to_csv().encode("utf-8")
                _date_str = datetime.now().strftime("%Y-%m-%d")
                threading.Thread(
                    target=minio_store.store_raw_csv,
                    args=(_csv_bytes, "prices", db_symbol, _date_str),
                    daemon=True,
                ).start()
            except Exception:
                pass

            # ── BACKUP: MongoDB raw doc — fire-and-forget daemon thread ──
            if mongo_store:
                try:
                    _mongo_doc = {
                        "symbol": db_symbol,
                        "source": "yfinance",
                        "currency": currency,
                        "rows": len(df),
                        "columns": list(df.columns),
                        "date_range": {
                            "start": str(df.index.min().date()),
                            "end": str(df.index.max().date()),
                        },
                        "stats": {
                            "avg_close": float(df["Close"].mean()) if "Close" in df else None,
                            "avg_volume": float(df["Volume"].mean()) if "Volume" in df else None,
                            "max_high": float(df["High"].max()) if "High" in df else None,
                            "min_low": float(df["Low"].min()) if "Low" in df else None,
                        },
                        "run_id": run_id,
                    }
                    threading.Thread(
                        target=mongo_store.store_document,
                        args=("raw_prices", _mongo_doc),
                        daemon=True,
                    ).start()
                except Exception:
                    pass

            if records:
                try:
                    n = db_client.upsert_daily_prices(records)
                    with _total_lock:
                        total_loaded += n
                    # Publish to Kafka — fire-and-forget daemon thread.
                    # kafka_producer.flush(timeout=10) blocks up to 10s per
                    # ticker; wrapping it as a daemon prevents it from
                    # exhausting the PostgreSQL connection pool when many
                    # workers are stuck waiting on Kafka ACKs (Fix 15).
                    if kafka_producer:
                        threading.Thread(
                            target=kafka_producer.publish_batch,
                            args=(
                                TOPICS.get("prices", "market.prices"),
                                records,
                            ),
                            kwargs={"key_field": "symbol"},
                            daemon=True,
                        ).start()
                    if metrics:
                        metrics.record_outcome("prices", db_symbol, "SUCCESS", n)
                    if progress_update:
                        progress_update(db_symbol, "SUCCESS")
                    db_client.insert_log(
                        _make_log_entry(
                            run_id,
                            "prices",
                            db_symbol,
                            "SUCCESS",
                            n,
                            frequency=frequency,
                            start=start_date,
                            end=end_date,
                        )
                    )
                except Exception as e:
                    if metrics:
                        metrics.record_outcome("prices", db_symbol, "FAILED")
                    if progress_update:
                        progress_update(db_symbol, "FAILED")
                    db_client.insert_log(
                        _make_log_entry(
                            run_id, "prices", db_symbol, "FAILED", 0, str(e), frequency, start_date, end_date
                        )
                    )
        else:
            # Delisted/failed ticker (Spec §7.2 Issue 4)
            if metrics:
                metrics.record_outcome("prices", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            db_client.insert_log(
                _make_log_entry(
                    run_id,
                    "prices",
                    db_symbol,
                    "SKIPPED",
                    0,
                    "No data returned from Yahoo Finance",
                    frequency,
                    start_date,
                    end_date,
                )
            )

    for i in range(0, len(ticker_map), batch_size):
        if _check_shutdown("prices batch"):
            break

        batch = ticker_map[i : i + batch_size]
        yf_tickers = [t[1] for t in batch]
        # Wrap download_batch in a daemon thread so a stuck HTTP socket
        # cannot block the prices phase indefinitely (Fix 24).
        # 90s = 3 retries × 30s timeout each — more than enough for any batch.
        _dl_result: dict = {}

        def _do_download_batch(_res=_dl_result):
            _res["data"] = downloader.download_batch(yf_tickers, start_date, end_date)

        _dl_thread = threading.Thread(target=_do_download_batch, daemon=True)
        _dl_thread.start()
        _dl_thread.join(timeout=90)
        if _dl_thread.is_alive():
            pipeline_logger.warning(
                f"Prices batch {i // batch_size}: download_batch timed out after "
                f"90s (stuck HTTP socket) — skipping batch and continuing"
            )
            batch_data = {}
        else:
            batch_data = _dl_result.get("data", {})

        # Parallel post-processing: MinIO + clean + DB upsert per ticker
        work_items = [(db_sym, yf_t, curr, batch_data.get(yf_t)) for db_sym, yf_t, curr in batch]

        pool = ThreadPoolExecutor(max_workers=post_workers)
        try:
            futures = [pool.submit(_process_ticker, item) for item in work_items]
            done, pending = futures_wait(futures, timeout=30)
            for future in done:
                try:
                    future.result()
                except Exception as e:
                    pipeline_logger.error(f"Price post-processing thread error: {e}")
            if pending:
                pipeline_logger.warning(
                    f"Price post-processing: {len(pending)} ticker(s) exceeded "
                    f"30s timeout — skipping to avoid pipeline stall"
                )
        finally:
            pool.shutdown(wait=False)

    pipeline_logger.info(f"Prices: loaded {total_loaded} records total")
    pipeline_logger.info(f"Prices downloader stats: {downloader.stats}")
    db_client.update_pipeline_metadata("prices", last_date=end_date)
    return downloader


def _run_fundamentals(
    db_client,
    minio_store,
    ticker_map,
    pipeline_params,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
    kafka_producer=None,
    mongo_store=None,
):
    """Download, clean, and load quarterly fundamental data.

    Retrieves quarterly balance sheet + income statement + key statistics
    per the spec (§2.1): book_value_per_share, net_income,
    shareholders_equity, total_debt, EPS.

    Uses ``ConcurrentDownloadExecutor`` for parallel ticker downloads.
    Thread count is kept modest (default: 4) to avoid triggering Yahoo
    Finance rate limits; the shared ``TokenBucketRateLimiter`` inside
    the downloader serialises burst requests across threads.
    """
    pipeline_logger.info("Starting fundamentals download...")
    max_workers = pipeline_params.get("fundamentals_workers", 2)
    dq = DataQualityChecker("fundamentals")
    total_loaded = 0
    _total_lock = threading.Lock()

    # Per-worker downloader instances — each thread gets its own
    # FundamentalsDownloader with its own TokenBucketRateLimiter so workers
    # don't compete for a single shared token bucket. With max_workers=4 each
    # running at api_delay=0.5s (2 tickers/sec), effective throughput is
    # max_workers × 2 = 8 tickers/sec instead of a shared 2 tickers/sec.
    _local_store = threading.local()
    _worker_downloaders: list = []
    _worker_dl_lock = threading.Lock()

    def _get_downloader() -> FundamentalsDownloader:
        if not hasattr(_local_store, "dl"):
            _local_store.dl = FundamentalsDownloader(
                api_delay=pipeline_params["api_delay_seconds"],
                max_retries=pipeline_params["max_retries"],
                backoff_base=pipeline_params["backoff_base"],
            )
            with _worker_dl_lock:
                _worker_downloaders.append(_local_store.dl)
        return _local_store.dl

    def _process_ticker(ticker_tuple):
        """Download + clean + insert fundamentals for one ticker (thread-safe)."""
        nonlocal total_loaded
        db_symbol, yf_ticker, currency = ticker_tuple

        if _check_shutdown("fundamentals"):
            return

        if db_symbol in _inactive_tickers:
            if metrics:
                metrics.record_outcome("fundamentals", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            return

        fund_data = _get_downloader().download(yf_ticker)
        if fund_data is not None:
            records = clean_fundamentals_data(fund_data, db_symbol, currency)
            dq.log_report(dq.check_fundamentals_records(records), db_symbol)

            if records:
                try:
                    raw_payload = {
                        "symbol": db_symbol,
                        "currency": currency,
                        "info": fund_data.get("info", {}),
                        "records_produced": len(records),
                    }
                    for stmt_key in [
                        "annual_balance_sheet",
                        "annual_income_stmt",
                        "annual_cash_flow",
                        "quarterly_balance_sheet",
                        "quarterly_income_stmt",
                        "quarterly_cash_flow",
                    ]:
                        stmt = fund_data.get(stmt_key)
                        if stmt is not None and isinstance(stmt, pd.DataFrame) and not stmt.empty:
                            raw_payload[stmt_key] = stmt.to_dict()
                        else:
                            raw_payload[stmt_key] = {}
                    minio_store.store_raw_json(
                        raw_payload, "fundamentals", db_symbol, datetime.now().strftime("%Y-%m-%d")
                    )
                    # Store raw in MongoDB (semi-structured archive)
                    if mongo_store:
                        info = fund_data.get("info", {})
                        mongo_store.store_document(
                            "raw_fundamentals",
                            {
                                "symbol": db_symbol,
                                "source": "yfinance",
                                "currency": currency,
                                "records_produced": len(records),
                                "fields_extracted": list({r["field_name"] for r in records}),
                                "period_types": list({r["period_type"] for r in records}),
                                "info_keys_available": list(info.keys()) if info else [],
                                "company_name": info.get("longName", info.get("shortName", "")),
                                "sector": info.get("sector", ""),
                                "industry": info.get("industry", ""),
                                "market_cap": info.get("marketCap"),
                                "run_id": run_id,
                            },
                        )
                    n = db_client.upsert_fundamentals(records)
                    with _total_lock:
                        total_loaded += n
                    # Publish to Kafka — fire-and-forget (Fix 15)
                    if kafka_producer:
                        threading.Thread(
                            target=kafka_producer.publish_batch,
                            args=(TOPICS.get("fundamentals", "market.fundamentals"), records),
                            kwargs={"key_field": "symbol"},
                            daemon=True,
                        ).start()
                    if metrics:
                        metrics.record_outcome("fundamentals", db_symbol, "SUCCESS", n)
                    if progress_update:
                        progress_update(db_symbol, "SUCCESS")
                    db_client.insert_log(
                        _make_log_entry(run_id, "fundamentals", db_symbol, "SUCCESS", n, frequency=frequency)
                    )
                except Exception as e:
                    if metrics:
                        metrics.record_outcome("fundamentals", db_symbol, "FAILED")
                    if progress_update:
                        progress_update(db_symbol, "FAILED")
                    db_client.insert_log(
                        _make_log_entry(run_id, "fundamentals", db_symbol, "FAILED", 0, str(e), frequency)
                    )
        else:
            if metrics:
                metrics.record_outcome("fundamentals", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            db_client.insert_log(
                _make_log_entry(run_id, "fundamentals", db_symbol, "SKIPPED", 0, frequency=frequency)
            )

    # Execute ticker downloads concurrently
    executor = ConcurrentDownloadExecutor(
        max_workers=max_workers,
        name="fundamentals-parallel",
    )
    executor.map_with_progress(
        fn=_process_ticker,
        items=list(ticker_map),
        result_key=lambda t: t[0],  # db_symbol as key
    )

    pipeline_logger.info(f"Fundamentals: loaded {total_loaded} records total")
    for _dl in _worker_downloaders:
        pipeline_logger.info(f"Fundamentals downloader stats: {_dl.stats}")
    db_client.update_pipeline_metadata("fundamentals")
    return _worker_downloaders


def _run_edgar_fundamentals(
    db_client,
    minio_store,
    ticker_map,
    pipeline_params,
    start_date,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
    kafka_producer=None,
    mongo_store=None,
):
    """Supplement quarterly fundamentals with SEC EDGAR XBRL data.

    EDGAR provides 5+ years of 10-Q filings for US companies,
    filling the gap left by Yahoo Finance (~1.7 years of quarterly data).
    Only processes US tickers (no exchange suffix).
    """
    us_tickers = [(db, yf, cur) for db, yf, cur in ticker_map if is_us_ticker(db)]

    if not us_tickers:
        pipeline_logger.info("EDGAR: no US tickers to process")
        return None

    pipeline_logger.info(f"Starting EDGAR fundamentals for {len(us_tickers)} US tickers...")
    downloader = EdgarFundamentalsDownloader(
        api_delay=pipeline_params.get("edgar_api_delay", 0.12),
        max_retries=pipeline_params["max_retries"],
        backoff_base=pipeline_params["backoff_base"],
    )
    edgar_workers = pipeline_params.get("edgar_workers", 6)
    total_loaded = 0
    _total_lock = threading.Lock()

    def _process_edgar(item):
        """Download + extract + store EDGAR data for one US ticker."""
        nonlocal total_loaded
        db_symbol, yf_ticker, currency = item
        if _check_shutdown("edgar_fundamentals"):
            return

        company_facts = downloader.download(db_symbol)
        if company_facts is not None:
            records = extract_edgar_fundamentals(company_facts, db_symbol, start_date=start_date)
            pipeline_logger.info(
                f"EDGAR {db_symbol}: extracted {len(records)} records " f"(start_date={start_date})"
            )

            # Store raw EDGAR response in MongoDB
            if mongo_store:
                facts = company_facts.get("facts", {})
                us_gaap = facts.get("us-gaap", {})
                mongo_store.store_document(
                    "raw_fundamentals",
                    {
                        "symbol": db_symbol,
                        "source": "sec_edgar",
                        "xbrl_concepts_found": len(us_gaap),
                        "records_extracted": len(records),
                        "entity_name": company_facts.get("entityName", ""),
                        "cik": company_facts.get("cik", ""),
                        "period_types": list({r["period_type"] for r in records}),
                        "fields_extracted": list({r["field_name"] for r in records}),
                        "date_range": {
                            "min": str(min(r["report_date"] for r in records)) if records else None,
                            "max": str(max(r["report_date"] for r in records)) if records else None,
                        },
                    },
                )

            if records:
                try:
                    minio_store.store_raw_json(
                        {"symbol": db_symbol, "source": "edgar", "records_produced": len(records)},
                        "edgar_fundamentals",
                        db_symbol,
                        datetime.now().strftime("%Y-%m-%d"),
                    )
                    n = db_client.upsert_fundamentals(records)
                    with _total_lock:
                        total_loaded += n
                    # Publish EDGAR records to Kafka — fire-and-forget (Fix 15)
                    if kafka_producer:
                        threading.Thread(
                            target=kafka_producer.publish_batch,
                            args=(TOPICS.get("fundamentals", "market.fundamentals"), records),
                            kwargs={"key_field": "symbol"},
                            daemon=True,
                        ).start()
                    if metrics:
                        metrics.record_outcome("edgar_fundamentals", db_symbol, "SUCCESS", n)
                    if progress_update:
                        progress_update(db_symbol, "SUCCESS")
                    db_client.insert_log(
                        _make_log_entry(
                            run_id, "edgar_fundamentals", db_symbol, "SUCCESS", n, frequency=frequency
                        )
                    )
                except Exception as e:
                    if metrics:
                        metrics.record_outcome("edgar_fundamentals", db_symbol, "FAILED")
                    if progress_update:
                        progress_update(db_symbol, "FAILED")
                    db_client.insert_log(
                        _make_log_entry(
                            run_id, "edgar_fundamentals", db_symbol, "FAILED", 0, str(e), frequency
                        )
                    )
            else:
                # Data downloaded but no records extracted after filtering
                pipeline_logger.debug(
                    f"EDGAR {db_symbol}: company_facts returned but "
                    f"0 records extracted (start_date={start_date})"
                )
                if metrics:
                    metrics.record_outcome("edgar_fundamentals", db_symbol, "SUCCESS", 0)
                if progress_update:
                    progress_update(db_symbol, "SUCCESS")
        else:
            if metrics:
                metrics.record_outcome("edgar_fundamentals", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")

    pool = ThreadPoolExecutor(max_workers=edgar_workers, thread_name_prefix="edgar-worker")
    edgar_futures = [pool.submit(_process_edgar, t) for t in us_tickers]
    done, pending = futures_wait(edgar_futures, timeout=120)
    if pending:
        pipeline_logger.warning(
            f"EDGAR: {len(pending)} workers still running after 120s timeout "
            f"— continuing (Fix 15 pattern)"
        )
    pool.shutdown(wait=False)

    pipeline_logger.info(f"EDGAR fundamentals: loaded {total_loaded} records total")
    pipeline_logger.info(f"EDGAR downloader stats: {downloader.stats}")
    db_client.update_pipeline_metadata("edgar_fundamentals")
    return downloader


def _run_finnhub_fundamentals(
    db_client,
    minio_store,
    ticker_map,
    pipeline_params,
    start_date,
    run_id,
    frequency,
    conf,
    metrics=None,
    progress_update=None,
    kafka_producer=None,
    mongo_store=None,
):
    """Supplement non-US quarterly+annual fundamentals with Finnhub data.

    Finnhub provides 5+ years of standardised financial statements for
    international tickers (.L, .PA, .DE, .MI, .AS, .TO, .SW) on the
    free tier (60 requests/min).
    """
    import os

    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        pipeline_logger.warning(
            "FINNHUB_API_KEY not set — skipping Finnhub fundamentals. "
            "Get a free key at https://finnhub.io/register"
        )
        # Mark every non-US ticker as SKIPPED so the progress bar
        # reflects real outcomes instead of showing 0/0/0.
        if progress_update:
            for db_symbol, _, _ in ticker_map:
                if is_non_us_ticker(db_symbol):
                    progress_update(db_symbol, "SKIPPED")
        return None

    non_us = [(db, yf, cur) for db, yf, cur in ticker_map if is_non_us_ticker(db)]

    if not non_us:
        pipeline_logger.info("Finnhub: no non-US tickers to process")
        return None

    pipeline_logger.info(f"Starting Finnhub fundamentals for {len(non_us)} non-US tickers...")
    downloader = FinnhubFundamentalsDownloader(
        api_key=api_key,
        api_delay=pipeline_params.get("finnhub_api_delay", 1.1),
        max_retries=pipeline_params["max_retries"],
        backoff_base=pipeline_params["backoff_base"],
    )
    # Keep workers modest — Finnhub free tier caps at 60 req/min.
    # The shared TokenBucketRateLimiter (rate≈0.91/s) serialises
    # actual API calls; parallelism overlaps processing with downloads.
    finnhub_workers = pipeline_params.get("finnhub_workers", 3)
    total_loaded = 0
    _total_lock = threading.Lock()

    def _process_finnhub(item):
        """Download + extract + store Finnhub data for one non-US ticker."""
        nonlocal total_loaded
        db_symbol, yf_ticker, currency = item
        if _check_shutdown("finnhub_fundamentals"):
            return

        reports = downloader.download(db_symbol)
        if reports is not None:
            records = extract_finnhub_fundamentals(
                reports, db_symbol, start_date=start_date, currency=currency
            )

            # Store raw Finnhub response in MongoDB
            if mongo_store:
                q_count = len(reports.get("quarterly", []))
                a_count = len(reports.get("annual", []))
                mongo_store.store_document(
                    "raw_fundamentals",
                    {
                        "symbol": db_symbol,
                        "source": "finnhub",
                        "currency": currency,
                        "quarterly_reports": q_count,
                        "annual_reports": a_count,
                        "records_extracted": len(records),
                        "fields_extracted": list({r["field_name"] for r in records}),
                        "period_types": list({r["period_type"] for r in records}),
                        "date_range": {
                            "min": str(min(r["report_date"] for r in records)) if records else None,
                            "max": str(max(r["report_date"] for r in records)) if records else None,
                        },
                    },
                )

            if records:
                try:
                    minio_store.store_raw_json(
                        {"symbol": db_symbol, "source": "finnhub", "records_produced": len(records)},
                        "finnhub_fundamentals",
                        db_symbol,
                        datetime.now().strftime("%Y-%m-%d"),
                    )
                    n = db_client.upsert_fundamentals(records)
                    with _total_lock:
                        total_loaded += n
                    # Publish Finnhub records to Kafka — fire-and-forget (Fix 15)
                    if kafka_producer:
                        threading.Thread(
                            target=kafka_producer.publish_batch,
                            args=(TOPICS.get("fundamentals", "market.fundamentals"), records),
                            kwargs={"key_field": "symbol"},
                            daemon=True,
                        ).start()
                    if metrics:
                        metrics.record_outcome("finnhub_fundamentals", db_symbol, "SUCCESS", n)
                    if progress_update:
                        progress_update(db_symbol, "SUCCESS")
                    db_client.insert_log(
                        _make_log_entry(
                            run_id, "finnhub_fundamentals", db_symbol, "SUCCESS", n, frequency=frequency
                        )
                    )
                except Exception as e:
                    if metrics:
                        metrics.record_outcome("finnhub_fundamentals", db_symbol, "FAILED")
                    if progress_update:
                        progress_update(db_symbol, "FAILED")
                    db_client.insert_log(
                        _make_log_entry(
                            run_id, "finnhub_fundamentals", db_symbol, "FAILED", 0, str(e), frequency
                        )
                    )
            else:
                # reports != None but extraction yielded 0 records
                if metrics:
                    metrics.record_outcome("finnhub_fundamentals", db_symbol, "SKIPPED")
                if progress_update:
                    progress_update(db_symbol, "SKIPPED")
        else:
            if metrics:
                metrics.record_outcome("finnhub_fundamentals", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")

    pool = ThreadPoolExecutor(max_workers=finnhub_workers, thread_name_prefix="finnhub-worker")
    # 175 tickers × 2 freq × 1.1s rate limit ≈ 385s serial; 3 workers ≈ 130s.
    # 120s was too short — raised to 500s so all tickers can complete.
    # Outer supplement_threads.join(600s) is the hard cap.
    finnhub_futures = [pool.submit(_process_finnhub, t) for t in non_us]
    done, pending = futures_wait(finnhub_futures, timeout=500)
    if pending:
        pipeline_logger.warning(
            f"Finnhub: {len(pending)} workers still running after 500s timeout "
            f"— continuing (Fix 15 pattern)"
        )
    pool.shutdown(wait=False)

    pipeline_logger.info(f"Finnhub fundamentals: loaded {total_loaded} records total")
    pipeline_logger.info(f"Finnhub downloader stats: {downloader.stats}")
    db_client.update_pipeline_metadata("finnhub_fundamentals")
    return downloader


def _run_nonus_fundamentals_supplement(
    db_client,
    minio_store,
    ticker_map,
    pipeline_params,
    start_date,
    run_id,
    frequency,
    conf,
    metrics=None,
    progress_update=None,
    kafka_producer=None,
    mongo_store=None,
):
    """Supplement non-US fundamentals with FMP, SimFin, and Alpha Vantage data.

    Runs a 3-source cascade for each non-US ticker:
      1. Financial Modeling Prep (fastest, 250 req/day)
      2. SimFin (2000 req/day, good international coverage)
      3. Alpha Vantage (4 keys rotated, 100 req/day total — last resort)

    Only processes non-US tickers. For each ticker, stops at the first
    source that returns data (no redundant downloads).
    """
    import os

    non_us = [(db, yf, cur) for db, yf, cur in ticker_map if is_non_us_ticker(db)]
    if not non_us:
        pipeline_logger.info("Non-US supplement: no non-US tickers to process")
        return None

    fmp_key = os.environ.get("FMP_API_KEY", "")
    simfin_key = os.environ.get("SIMFIN_API_KEY", "")
    av_keys = [os.environ.get(f"ALPHA_VANTAGE_KEY_{i}", "") for i in range(1, 5)]
    av_keys = [k for k in av_keys if k]

    if not fmp_key and not simfin_key and not av_keys:
        pipeline_logger.warning(
            "No FMP/SimFin/Alpha Vantage API keys set — skipping non-US fundamentals supplement"
        )
        if progress_update:
            for db_symbol, _, _ in non_us:
                progress_update(db_symbol, "SKIPPED")
        return None

    fmp_dl = FmpFundamentalsDownloader(api_delay=0.2) if fmp_key else None
    simfin_dl = SimFinFundamentalsDownloader(api_delay=0.5) if simfin_key else None
    av_dl = AlphaVantageFundamentalsDownloader(api_delay=3.1) if av_keys else None

    # ── Skip tickers already well-covered by yfinance/Finnhub ──
    # Skip tickers with ≥ 20 DISTINCT quarterly report dates (5+ years).
    # Previous bug: counted total records (fields × quarters), which inflated
    # the count — a ticker with 15 quarters × 12 fields = 180 records passed
    # the old >= 20 threshold despite having only 3.7 years of quarterly data.
    try:
        depth_rows = db_client.read_query(
            "SELECT TRIM(symbol), COUNT(DISTINCT report_date) AS quarters "
            "FROM systematic_equity.fundamentals "
            "WHERE period_type = 'quarterly' "
            "GROUP BY TRIM(symbol) "
            "HAVING COUNT(DISTINCT report_date) >= 20"
        )
        well_covered = {r[0] for r in depth_rows} if depth_rows else set()
    except Exception:
        well_covered = set()

    need_supplement = [t for t in non_us if t[0] not in well_covered and t[0] not in _inactive_tickers]

    pipeline_logger.info(
        f"Starting non-US fundamentals supplement for {len(need_supplement)}/{len(non_us)} tickers "
        f"({len(well_covered)} already well-covered, skipped) "
        f"(FMP={'✓' if fmp_dl else '✗'}, SimFin={'✓' if simfin_dl else '✗'}, "
        f"AV={'✓ ×' + str(len(av_keys)) if av_dl else '✗'})..."
    )

    # Mark well-covered tickers as SKIPPED in progress
    if progress_update:
        for db_symbol, _, _ in non_us:
            if db_symbol in well_covered:
                progress_update(db_symbol, "SKIPPED")

    total_loaded = 0
    _total_lock = threading.Lock()
    supplement_workers = pipeline_params.get("nonus_supplement_workers", 4)

    def _process_ticker(item):
        nonlocal total_loaded
        db_symbol, yf_ticker, currency = item
        if _check_shutdown("nonus_supplement"):
            return
        if db_symbol in _inactive_tickers:
            if metrics:
                metrics.record_outcome("nonus_supplement", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            db_client.insert_log(
                _make_log_entry(run_id, "nonus_supplement", db_symbol, "SKIPPED", 0, "inactive", frequency)
            )
            return

        records = []
        source_used = None

        # Cascade: FMP → SimFin → Alpha Vantage
        if fmp_dl and not records:
            try:
                records = fmp_dl.download(db_symbol, yf_ticker) or []
                if records:
                    source_used = "fmp"
            except Exception as e:
                pipeline_logger.debug(f"FMP failed for {db_symbol}: {e}")

        if simfin_dl and not records:
            try:
                records = simfin_dl.download(db_symbol, yf_ticker) or []
                if records:
                    source_used = "simfin"
            except Exception as e:
                pipeline_logger.debug(f"SimFin failed for {db_symbol}: {e}")

        if av_dl and not records:
            try:
                records = av_dl.download(db_symbol, yf_ticker) or []
                if records:
                    source_used = "alphavantage"
            except Exception as e:
                pipeline_logger.debug(f"Alpha Vantage failed for {db_symbol}: {e}")

        if records:
            try:
                n = db_client.upsert_fundamentals(records)
                with _total_lock:
                    total_loaded += n
                if mongo_store:
                    mongo_store.store_document(
                        "raw_fundamentals",
                        {
                            "symbol": db_symbol,
                            "source": source_used,
                            "records_produced": len(records),
                            "fields": list({r["field_name"] for r in records}),
                            "run_id": run_id,
                        },
                    )
                if metrics:
                    metrics.record_outcome("nonus_supplement", db_symbol, "SUCCESS", n)
                if progress_update:
                    progress_update(db_symbol, "SUCCESS")
                db_client.insert_log(
                    _make_log_entry(
                        run_id, "nonus_supplement", db_symbol, "SUCCESS", n,
                        frequency=frequency,
                    )
                )
            except Exception as e:
                if metrics:
                    metrics.record_outcome("nonus_supplement", db_symbol, "FAILED")
                if progress_update:
                    progress_update(db_symbol, "FAILED")
                db_client.insert_log(
                    _make_log_entry(
                        run_id, "nonus_supplement", db_symbol, "FAILED", 0,
                        str(e), frequency,
                    )
                )
                pipeline_logger.debug(f"Non-US supplement upsert failed for {db_symbol}: {e}")
        else:
            if metrics:
                metrics.record_outcome("nonus_supplement", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            db_client.insert_log(
                _make_log_entry(
                    run_id, "nonus_supplement", db_symbol, "SKIPPED", 0,
                    frequency=frequency,
                )
            )

    pool = ThreadPoolExecutor(max_workers=supplement_workers)
    try:
        futures = [pool.submit(_process_ticker, item) for item in need_supplement]
        done, pending = futures_wait(futures, timeout=600)
        for future in done:
            try:
                future.result()
            except Exception as e:
                pipeline_logger.error(f"Non-US supplement thread error: {e}")
        if pending:
            pipeline_logger.warning(
                f"Non-US supplement: {len(pending)} tickers exceeded timeout"
            )
    finally:
        pool.shutdown(wait=False)

    pipeline_logger.info(f"Non-US supplement: loaded {total_loaded} records total")
    db_client.update_pipeline_metadata("nonus_supplement")
    all_dls = [d for d in [fmp_dl, simfin_dl, av_dl] if d]
    return all_dls


def _compute_historical_ratios(
    db_client,
    ticker_map,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
):
    """Compute historical financial ratios from fundamentals + daily_prices.

    Derives 6-year time-series ratios by joining the fundamentals EAV table
    with daily price data. This produces historical P/E, D/E, ROE, margins,
    and other ratios that cannot be obtained from yfinance snapshots.

    Inserts into the company_ratios table (same EAV schema as yfinance
    snapshot ratios) with snapshot_date set to the fundamentals report_date.
    """
    from datetime import date as _date
    import bisect

    # No skip-if-done check: this phase is DB-only (no API calls, ~35s total).
    # Always recompute to ensure new ratio formulas are applied to all tickers.
    # upsert_company_ratios uses ON CONFLICT DO UPDATE — safe for re-runs.
    need_compute = [t for t in ticker_map if t[0] not in _inactive_tickers]
    pipeline_logger.info(
        f"Computing historical ratios from fundamentals + prices "
        f"({len(need_compute)}/{len(ticker_map)} tickers)..."
    )

    total_loaded = 0
    _total_lock = threading.Lock()
    hist_workers = 8

    def _process_ticker(item):
        nonlocal total_loaded
        db_symbol, yf_ticker, currency = item
        if _check_shutdown("historical_ratios"):
            return
        if db_symbol in _inactive_tickers:
            if metrics:
                metrics.record_outcome("historical_ratios", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            db_client.insert_log(
                _make_log_entry(run_id, "historical_ratios", db_symbol, "SKIPPED", 0, "inactive", frequency)
            )
            return

        try:
            # Fetch all fundamentals for this ticker
            fund_rows = db_client.read_query(
                "SELECT field_name, field_value, report_date, period_type "
                "FROM systematic_equity.fundamentals "
                "WHERE TRIM(symbol) = :sym "
                "ORDER BY report_date",
                {"sym": db_symbol},
            )
            if not fund_rows:
                if metrics:
                    metrics.record_outcome("historical_ratios", db_symbol, "SKIPPED")
                if progress_update:
                    progress_update(db_symbol, "SKIPPED")
                return

            # Build lookup: (field_name, report_date, period_type) -> value
            fund_lookup = {}
            report_dates = set()
            for fname, fval, rdate, ptype in fund_rows:
                fund_lookup[(fname, rdate, ptype)] = float(fval) if fval is not None else None
                report_dates.add((rdate, ptype))

            # Fetch closest price for each report date
            price_rows = db_client.read_query(
                "SELECT cob_date, close_price "
                "FROM systematic_equity.daily_prices "
                "WHERE TRIM(symbol) = :sym AND close_price IS NOT NULL "
                "ORDER BY cob_date",
                {"sym": db_symbol},
            )
            price_lookup = {}
            if price_rows:
                for cob, close in price_rows:
                    price_lookup[cob] = float(close)

            # Fetch shares_outstanding from ratios table as fallback
            # (more reliable than deriving from equity / book_value)
            shares_fallback = None
            try:
                shares_rows = db_client.read_query(
                    "SELECT field_value FROM systematic_equity.company_ratios "
                    "WHERE TRIM(symbol) = :sym AND field_name = 'shares_outstanding' "
                    "ORDER BY snapshot_date DESC LIMIT 1",
                    {"sym": db_symbol},
                )
                if shares_rows and shares_rows[0][0]:
                    shares_fallback = float(shares_rows[0][0])
            except Exception:
                pass

            # For each report period, compute ratios
            records = []
            seen = set()
            sorted_prices = sorted(price_lookup.keys()) if price_lookup else []

            for report_date, period_type in sorted(report_dates):
                # Find closest price on or after report_date
                close_price = None
                if sorted_prices:
                    idx = bisect.bisect_left(sorted_prices, report_date)
                    if idx < len(sorted_prices):
                        close_price = price_lookup[sorted_prices[idx]]
                    elif idx > 0:
                        close_price = price_lookup[sorted_prices[idx - 1]]

                def _get(field):
                    return fund_lookup.get((field, report_date, period_type))

                def _add(field_name, value):
                    if value is not None and not (isinstance(value, float) and (abs(value) > 1e15 or value != value)):
                        key = (field_name, report_date)
                        if key not in seen:
                            seen.add(key)
                            records.append({
                                "symbol": db_symbol,
                                "snapshot_date": report_date,
                                "field_name": field_name,
                                "field_value": round(value, 6),
                            })

                net_income = _get("net_income")
                equity = _get("stockholders_equity")
                total_debt = _get("total_debt")
                total_rev = _get("total_revenue")
                operating_inc = _get("operating_income")
                ebitda_val = _get("ebitda")
                total_assets = _get("total_assets")
                total_liab = _get("total_liabilities")
                ocf = _get("operating_cash_flow")
                capex = _get("capital_expenditure")
                fcf = _get("free_cash_flow")
                diluted_eps = _get("diluted_eps")
                basic_eps = _get("basic_eps")
                gross_profit = _get("gross_profit")
                book_val = _get("book_value")

                # ── Derive missing fields from available data ──
                # equity fallback: total_assets - total_liabilities
                if equity is None and total_assets and total_liab:
                    equity = total_assets - total_liab

                # EPS fallback: use basic_eps when diluted_eps missing
                eps = diluted_eps if diluted_eps is not None else basic_eps

                # FCF fallback: operating_cash_flow - |capital_expenditure|
                if fcf is None and ocf is not None and capex is not None:
                    fcf = ocf - abs(capex)

                # Derive shares: prefer equity/book_value, fallback to shares_outstanding
                shares = None
                if book_val and book_val != 0 and equity:
                    shares = equity / book_val
                if (shares is None or shares <= 0) and shares_fallback:
                    shares = shares_fallback

                # Market cap (reused across multiple ratios)
                mcap = None
                if close_price and shares and shares > 0:
                    mcap = close_price * shares

                # ── P/E ratio ──
                # Primary: price / diluted_eps
                # Fallback: price / basic_eps
                if close_price and eps and eps != 0:
                    _add("pe_ratio_hist", close_price / eps)

                # ── D/E ratio ──
                # Primary: total_debt / stockholders_equity
                # Fallback: total_debt / (total_assets - total_liabilities)
                if total_debt is not None and equity and equity != 0:
                    _add("debt_to_equity_hist", total_debt / equity)

                # ── ROE ──
                # Primary: net_income / stockholders_equity
                # Fallback: net_income / (total_assets - total_liabilities)
                if net_income is not None and equity and equity != 0:
                    _add("roe_hist", net_income / equity)

                # ── Profit margin ──
                if net_income is not None and total_rev and total_rev != 0:
                    _add("profit_margin_hist", net_income / total_rev)

                # ── Gross margin ──
                # Primary: gross_profit / total_revenue
                # Fallback: (total_revenue - (total_revenue - gross_profit)) — circular
                # No valid fallback without cost_of_revenue
                if gross_profit is not None and total_rev and total_rev != 0:
                    _add("gross_margin_hist", gross_profit / total_rev)

                # ── Operating margin ──
                # Primary: operating_income / total_revenue
                # Fallback 1: ebitda / total_revenue (EBITDA margin)
                # Fallback 2: (net_income + interest + tax) / total_revenue — no interest/tax fields
                if operating_inc is not None and total_rev and total_rev != 0:
                    _add("operating_margin_hist", operating_inc / total_rev)
                elif ebitda_val is not None and total_rev and total_rev != 0:
                    _add("operating_margin_hist", ebitda_val / total_rev)

                # ── EV/EBITDA ──
                # EV = market_cap + total_debt
                if ebitda_val and ebitda_val != 0 and mcap and total_debt is not None:
                    ev = mcap + total_debt
                    _add("ev_to_ebitda_hist", ev / ebitda_val)

                # ── ROA ──
                if net_income is not None and total_assets and total_assets != 0:
                    _add("roa_hist", net_income / total_assets)

                # ── Assets to liabilities ──
                if total_assets and total_liab and total_liab != 0:
                    _add("assets_to_liab_hist", total_assets / total_liab)

                # ── Debt to assets ──
                if total_debt is not None and total_assets and total_assets != 0:
                    _add("debt_to_assets_hist", total_debt / total_assets)

                # ── Equity ratio (equity / total_assets) ──
                if equity and total_assets and total_assets != 0:
                    _add("equity_ratio_hist", equity / total_assets)

                # ── FCF yield ──
                # Primary: free_cash_flow / market_cap
                # Fallback: (operating_cash_flow - |capex|) / market_cap
                if fcf is not None and mcap and mcap > 0:
                    _add("fcf_yield_hist", fcf / mcap)

                # ── FCF margin ──
                if fcf is not None and total_rev and total_rev != 0:
                    _add("fcf_margin_hist", fcf / total_rev)

                # ── OCF to debt (cash flow coverage) ──
                if ocf is not None and total_debt and total_debt != 0:
                    _add("ocf_to_debt_hist", ocf / total_debt)

                # ── Earnings yield (E/P) ──
                # Primary: diluted_eps / price
                # Fallback: basic_eps / price
                if close_price and close_price != 0 and eps is not None:
                    _add("earnings_to_price_hist", eps / close_price)

                # ── Cashflow to price (OCF/P) ──
                if ocf is not None and mcap and mcap > 0:
                    _add("cashflow_to_price_hist", ocf / mcap)

                # ── Revenue to market cap (sales yield) ──
                if total_rev and mcap and mcap > 0:
                    _add("revenue_to_mcap_hist", total_rev / mcap)

                # ── EBITDA margin ──
                if ebitda_val is not None and total_rev and total_rev != 0:
                    _add("ebitda_margin_hist", ebitda_val / total_rev)

                # ── Interest coverage proxy (EBITDA / interest) ──
                # interest ≈ operating_income - net_income (very rough, includes tax)
                # Better: EBITDA / (total_debt * assumed_rate) — too speculative
                # Skip: no clean derivation without explicit interest expense

                # ── Book to price ──
                # Primary: book_value_per_share / price
                # Fallback: (equity / shares) / price
                bvps = book_val
                if bvps is None and equity and shares and shares > 0:
                    bvps = equity / shares
                if close_price and close_price != 0 and bvps and bvps > 0:
                    _add("book_to_price_hist", bvps / close_price)

                # ── Price to book (inverse of book_to_price) ──
                if close_price and bvps and bvps > 0:
                    _add("price_to_book_hist", close_price / bvps)

                # ── Revenue growth (sequential QoQ/YoY) ──
                # Handled in the sequential loop below

            # ── Sequential growth metrics: QoQ comparison ──
            quarterly_dates = sorted(
                [rd for rd, pt in report_dates if pt == "quarterly"]
            )
            for i in range(1, len(quarterly_dates)):
                prev_date = quarterly_dates[i - 1]
                curr_date = quarterly_dates[i]

                # Earnings growth (EPS QoQ)
                prev_eps = fund_lookup.get(("diluted_eps", prev_date, "quarterly"))
                curr_eps = fund_lookup.get(("diluted_eps", curr_date, "quarterly"))
                if prev_eps is None:
                    prev_eps = fund_lookup.get(("basic_eps", prev_date, "quarterly"))
                if curr_eps is None:
                    curr_eps = fund_lookup.get(("basic_eps", curr_date, "quarterly"))
                if prev_eps is not None and curr_eps is not None and prev_eps != 0:
                    growth = (curr_eps - prev_eps) / abs(prev_eps)
                    if abs(growth) < 100:
                        _add_seq = lambda fn, val, dt=curr_date: (
                            records.append({"symbol": db_symbol, "snapshot_date": dt,
                                            "field_name": fn, "field_value": round(val, 6)})
                            if (fn, dt) not in seen and seen.add((fn, dt)) is None else None
                        )
                        key = ("earnings_growth_hist", curr_date)
                        if key not in seen:
                            seen.add(key)
                            records.append({
                                "symbol": db_symbol,
                                "snapshot_date": curr_date,
                                "field_name": "earnings_growth_hist",
                                "field_value": round(growth, 6),
                            })

                # Revenue growth (QoQ)
                prev_rev = fund_lookup.get(("total_revenue", prev_date, "quarterly"))
                curr_rev = fund_lookup.get(("total_revenue", curr_date, "quarterly"))
                if prev_rev is not None and curr_rev is not None and prev_rev != 0:
                    rev_growth = (curr_rev - prev_rev) / abs(prev_rev)
                    if abs(rev_growth) < 100:
                        key = ("revenue_growth_hist", curr_date)
                        if key not in seen:
                            seen.add(key)
                            records.append({
                                "symbol": db_symbol,
                                "snapshot_date": curr_date,
                                "field_name": "revenue_growth_hist",
                                "field_value": round(rev_growth, 6),
                            })

                # Net income growth (QoQ)
                prev_ni = fund_lookup.get(("net_income", prev_date, "quarterly"))
                curr_ni = fund_lookup.get(("net_income", curr_date, "quarterly"))
                if prev_ni is not None and curr_ni is not None and prev_ni != 0:
                    ni_growth = (curr_ni - prev_ni) / abs(prev_ni)
                    if abs(ni_growth) < 100:
                        key = ("net_income_growth_hist", curr_date)
                        if key not in seen:
                            seen.add(key)
                            records.append({
                                "symbol": db_symbol,
                                "snapshot_date": curr_date,
                                "field_name": "net_income_growth_hist",
                                "field_value": round(ni_growth, 6),
                            })

                # OCF growth (QoQ)
                prev_ocf = fund_lookup.get(("operating_cash_flow", prev_date, "quarterly"))
                curr_ocf = fund_lookup.get(("operating_cash_flow", curr_date, "quarterly"))
                if prev_ocf is not None and curr_ocf is not None and prev_ocf != 0:
                    ocf_growth = (curr_ocf - prev_ocf) / abs(prev_ocf)
                    if abs(ocf_growth) < 100:
                        key = ("ocf_growth_hist", curr_date)
                        if key not in seen:
                            seen.add(key)
                            records.append({
                                "symbol": db_symbol,
                                "snapshot_date": curr_date,
                                "field_name": "ocf_growth_hist",
                                "field_value": round(ocf_growth, 6),
                            })

            if records:
                n = db_client.upsert_company_ratios(records)
                with _total_lock:
                    total_loaded += n
                if metrics:
                    metrics.record_outcome("historical_ratios", db_symbol, "SUCCESS", n)
                if progress_update:
                    progress_update(db_symbol, "SUCCESS")
                db_client.insert_log(
                    _make_log_entry(
                        run_id, "historical_ratios", db_symbol, "SUCCESS", n,
                        frequency=frequency,
                    )
                )
            else:
                if metrics:
                    metrics.record_outcome("historical_ratios", db_symbol, "SKIPPED")
                if progress_update:
                    progress_update(db_symbol, "SKIPPED")
        except Exception as e:
            if metrics:
                metrics.record_outcome("historical_ratios", db_symbol, "FAILED")
            if progress_update:
                progress_update(db_symbol, "FAILED")
            db_client.insert_log(
                _make_log_entry(
                    run_id, "historical_ratios", db_symbol, "FAILED", 0,
                    str(e), frequency,
                )
            )
            pipeline_logger.debug(f"Historical ratios failed for {db_symbol}: {e}")

    pool = ThreadPoolExecutor(max_workers=hist_workers)
    try:
        futures = [pool.submit(_process_ticker, item) for item in need_compute]
        done, pending = futures_wait(futures, timeout=300)
        for future in done:
            try:
                future.result()
            except Exception as e:
                pipeline_logger.error(f"Historical ratios thread error: {e}")
    finally:
        pool.shutdown(wait=False)

    pipeline_logger.info(f"Historical ratios: computed {total_loaded} records total")
    db_client.update_pipeline_metadata("historical_ratios")


def _backfill_historical_sentiment(
    db_client,
    mongo_store,
    ticker_map,
    pipeline_params,
    start_date,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
):
    """Backfill historical sentiment from GDELT for tickers with no history.

    Checks the news_sentiment table for each ticker. If a ticker has fewer
    than 4 historical records, queries GDELT for quarterly sentiment data
    going back to start_date (6 years).

    GDELT DOC 2.0 API supports historical queries via the timespan parameter.
    We query one quarter at a time per ticker to get representative coverage.
    """
    from datetime import date as _date
    from dateutil.relativedelta import relativedelta

    pipeline_logger.info("Checking for sentiment backfill candidates...")

    # Find tickers needing backfill — check DISTINCT YEARS, not total records.
    # A ticker with 6 records all in 2026 needs backfill for 2020-2025.
    # A ticker with records in 4+ distinct years is well-covered.
    try:
        existing = db_client.read_query(
            "SELECT TRIM(symbol), COUNT(DISTINCT EXTRACT(YEAR FROM cob_date)) AS yr_cnt "
            "FROM systematic_equity.news_sentiment "
            "GROUP BY TRIM(symbol)"
        )
        existing_years = {row[0]: int(row[1]) for row in existing} if existing else {}
    except Exception:
        existing_years = {}

    backfill_tickers = []
    for db_symbol, yf_ticker, currency in ticker_map:
        if db_symbol in _inactive_tickers:
            if metrics:
                metrics.record_outcome("sentiment_backfill", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            continue
        yr_cnt = existing_years.get(db_symbol, 0)
        if yr_cnt >= 4:
            if metrics:
                metrics.record_outcome("sentiment_backfill", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            continue
        backfill_tickers.append((db_symbol, yf_ticker, currency))

    if not backfill_tickers:
        pipeline_logger.info("Sentiment backfill: all tickers have sufficient history")
        if progress_update:
            for db_symbol, _, _ in ticker_map:
                progress_update(db_symbol, "SKIPPED")
        return

    pipeline_logger.info(
        f"Sentiment backfill: {len(backfill_tickers)} tickers need historical data "
        f"(querying GDELT quarterly from {start_date})..."
    )

    # Generate quarterly date ranges from start_date to now
    try:
        start_dt = _date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    except (ValueError, TypeError):
        start_dt = _date(2020, 3, 1)

    quarters = []
    q_start = _date(start_dt.year, ((start_dt.month - 1) // 3) * 3 + 1, 1)
    now = _date.today()
    while q_start < now:
        q_end = q_start + relativedelta(months=3) - timedelta(days=1)
        if q_end > now:
            q_end = now
        quarters.append((q_start, q_end))
        q_start = q_start + relativedelta(months=3)

    gdelt = GdeltDownloader(
        api_delay=0.1,   # GDELT has no rate limit — free, open API
        max_retries=2,
        backoff_base=2.0,
        max_articles=15,
        timeout=15,
    )

    # Load company names for better GDELT search queries.
    # Searching "Iron Mountain" finds far more articles than "IRM".
    try:
        name_rows = db_client.read_query(
            "SELECT TRIM(symbol), security FROM systematic_equity.company_static"
        )
        company_names = {r[0]: r[1] for r in name_rows} if name_rows else {}
    except Exception:
        company_names = {}

    total_loaded = 0
    _total_lock = threading.Lock()
    backfill_workers = pipeline_params.get("backfill_workers", 12)

    def _backfill_ticker(item):
        nonlocal total_loaded
        db_symbol, yf_ticker, currency = item
        if _check_shutdown("sentiment_backfill"):
            return

        # Check which quarters already have sentiment data for this ticker
        try:
            existing_dates = db_client.read_query(
                "SELECT cob_date FROM systematic_equity.news_sentiment "
                "WHERE TRIM(symbol) = :sym",
                {"sym": db_symbol},
            )
            existing_cob = {r[0] for r in existing_dates} if existing_dates else set()
        except Exception:
            existing_cob = set()

        ticker_records = 0
        for q_start_dt, q_end_dt in quarters:
            # Skip quarters that already have a sentiment record
            mid = q_start_dt + (q_end_dt - q_start_dt) / 2
            if mid in existing_cob:
                continue
            if _check_shutdown("sentiment_backfill"):
                break

            try:
                # Multi-strategy GDELT search cascade:
                # 1. "Company Name" (exact match — best precision)
                # 2. Company Name (unquoted — broader match)
                # 3. Ticker symbol (catches financial news)
                company_name = company_names.get(db_symbol, "")
                clean_name = ""
                if company_name:
                    clean_name = company_name.split(",")[0].split(" Inc")[0]
                    clean_name = clean_name.split(" Corp")[0].split(" Ltd")[0]
                    clean_name = clean_name.split(" plc")[0].split(" PLC")[0]
                    clean_name = clean_name.split(" SE")[0].split(" SA")[0]
                    clean_name = clean_name.split(" AG")[0].split(" NV")[0]
                    clean_name = clean_name.strip()

                base_symbol = db_symbol.split(".")[0]
                queries = []
                if clean_name and len(clean_name) > 2:
                    queries.append(f'"{clean_name}"')    # exact match
                    queries.append(clean_name)            # broad match
                queries.append(base_symbol)               # ticker symbol

                articles = None
                for query in queries:
                    gdelt.rate_limiter.acquire()
                    resp = requests.get(
                        "https://api.gdeltproject.org/api/v2/doc/doc",
                        params={
                            "query": query,
                            "mode": "ArtList",
                            "maxrecords": 15,
                            "format": "json",
                            "sourcelang": "english",
                            "startdatetime": q_start_dt.strftime("%Y%m%d%H%M%S"),
                            "enddatetime": q_end_dt.strftime("%Y%m%d%H%M%S"),
                        },
                        headers={"User-Agent": "KolmogorovTeam/1.0"},
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        found = data.get("articles", [])
                        if found:
                            articles = found
                            break

                # If all queries returned nothing, record neutral sentiment
                # (no news = no signal = neutral score of 50.0)
                if not articles:
                    agg = {
                        "symbol": db_symbol,
                        "cob_date": mid.isoformat(),
                        "article_count": 0,
                        "avg_sentiment": 0.0,
                        "positive_count": 0,
                        "negative_count": 0,
                        "neutral_count": 0,
                        "max_sentiment": 0.0,
                        "min_sentiment": 0.0,
                        "positive_ratio": 0.0,
                        "sentiment_score": 50.0,
                        "score_dispersion": 0.0,
                    }
                    n = db_client.upsert_news_sentiment([agg])
                    with _total_lock:
                        total_loaded += n
                    ticker_records += n
                    continue

                # Process found articles through the standard scoring pipeline
                parsed = parse_gdelt_articles(articles, db_symbol)
                if not parsed:
                    continue

                parsed = deduplicate_articles(parsed)
                scored = score_articles(parsed)
                agg = aggregate_sentiment(scored, db_symbol)
                if agg:
                    agg["cob_date"] = mid.isoformat()
                    n = db_client.upsert_news_sentiment([agg])
                    with _total_lock:
                        total_loaded += n
                    ticker_records += n

            except Exception as e:
                pipeline_logger.debug(
                    f"GDELT backfill {db_symbol} Q{q_start_dt}: {e}"
                )
                continue

        if ticker_records > 0:
            if metrics:
                metrics.record_outcome("sentiment_backfill", db_symbol, "SUCCESS", ticker_records)
            if progress_update:
                progress_update(db_symbol, "SUCCESS")
            db_client.insert_log(
                _make_log_entry(
                    run_id, "sentiment_backfill", db_symbol, "SUCCESS",
                    ticker_records, frequency=frequency,
                )
            )
        else:
            if metrics:
                metrics.record_outcome("sentiment_backfill", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            db_client.insert_log(
                _make_log_entry(
                    run_id, "sentiment_backfill", db_symbol, "SKIPPED",
                    0, "no GDELT articles found", frequency,
                )
            )

    pool = ThreadPoolExecutor(max_workers=backfill_workers)
    try:
        futures = [pool.submit(_backfill_ticker, item) for item in backfill_tickers]
        done, pending = futures_wait(futures, timeout=1800)
        for future in done:
            try:
                future.result()
            except Exception as e:
                pipeline_logger.error(f"Sentiment backfill thread error: {e}")
        if pending:
            pipeline_logger.warning(
                f"Sentiment backfill: {len(pending)} tickers exceeded 30min timeout"
            )
    finally:
        pool.shutdown(wait=False)

    pipeline_logger.info(f"Sentiment backfill: loaded {total_loaded} records total")
    db_client.update_pipeline_metadata("sentiment_backfill")


def _run_fx(
    db_client,
    minio_store,
    pipeline_params,
    start_date,
    end_date,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
    kafka_producer=None,
    mongo_store=None,
):
    """Download, clean, and load daily FX rate data.

    Downloads GBPUSD=X, EURUSD=X, CADUSD=X, CHFUSD=X as specified
    in Spec §7.5.
    """
    pipeline_logger.info("Starting FX rate download...")
    downloader = FxDownloader(
        api_delay=pipeline_params["api_delay_seconds"],
        max_retries=pipeline_params["max_retries"],
        backoff_base=pipeline_params["backoff_base"],
    )
    dq = DataQualityChecker("fx")
    fx_data = downloader.download_all(start_date, end_date)
    total_loaded = 0

    for pair, df in fx_data.items():
        if _check_shutdown("fx"):
            break

        try:
            minio_store.store_raw_csv(
                df.to_csv().encode("utf-8"), "fx", pair.replace("=", ""), datetime.now().strftime("%Y-%m-%d")
            )
        except Exception:
            pass

        # Store raw in MongoDB (semi-structured archive)
        if mongo_store:
            mongo_store.store_document(
                "raw_fx",
                {
                    "pair": pair,
                    "source": "yfinance",
                    "rows": len(df),
                    "date_range": {
                        "start": str(df.index.min().date()),
                        "end": str(df.index.max().date()),
                    },
                    "stats": {
                        "avg_close": float(df["Close"].iloc[:, 0].mean()) if "Close" in df else None,
                        "latest_close": float(df["Close"].iloc[-1, 0]) if "Close" in df else None,
                    },
                    "run_id": run_id,
                },
            )

        records = clean_fx_dataframe(df, pair)
        dq.log_report(dq.check_fx_records(records), pair)

        if records:
            try:
                n = db_client.upsert_fx_rates(records)
                total_loaded += n
                # Publish to Kafka — fire-and-forget (Fix 15)
                if kafka_producer:
                    threading.Thread(
                        target=kafka_producer.publish_batch,
                        args=(TOPICS.get("fx", "market.fx"), records),
                        kwargs={"key_field": "currency_pair"},
                        daemon=True,
                    ).start()
                if metrics:
                    metrics.record_outcome("fx", pair, "SUCCESS", n)
                if progress_update:
                    progress_update(pair, "SUCCESS")
                db_client.insert_log(
                    _make_log_entry(
                        run_id, "fx", pair, "SUCCESS", n, frequency=frequency, start=start_date, end=end_date
                    )
                )
            except Exception as e:
                if metrics:
                    metrics.record_outcome("fx", pair, "FAILED")
                if progress_update:
                    progress_update(pair, "FAILED")
                db_client.insert_log(
                    _make_log_entry(run_id, "fx", pair, "FAILED", 0, str(e), frequency, start_date, end_date)
                )

    pipeline_logger.info(f"FX rates: loaded {total_loaded} records total")
    pipeline_logger.info(f"FX downloader stats: {downloader.stats}")
    db_client.update_pipeline_metadata("fx", last_date=end_date)
    return downloader


def _run_vix(
    db_client,
    minio_store,
    pipeline_params,
    start_date,
    end_date,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
    kafka_producer=None,
    mongo_store=None,
):
    """Download, clean, and load daily VIX index data.

    Required for volatility regime classification in CW2 (Spec §4.4).
    """
    pipeline_logger.info("Starting VIX download...")
    downloader = VixDownloader(
        api_delay=pipeline_params["api_delay_seconds"],
        max_retries=pipeline_params["max_retries"],
        backoff_base=pipeline_params["backoff_base"],
    )
    dq = DataQualityChecker("vix")
    df = downloader.download(start_date, end_date)

    if not df.empty:
        try:
            minio_store.store_raw_csv(
                df.to_csv().encode("utf-8"), "vix", "VIX", datetime.now().strftime("%Y-%m-%d")
            )
        except Exception:
            pass

        # Store raw in MongoDB (semi-structured archive)
        if mongo_store:
            mongo_store.store_document(
                "raw_macro",
                {
                    "symbol": "^VIX",
                    "source": "yfinance",
                    "data_type": "vix",
                    "rows": len(df),
                    "date_range": {
                        "start": str(df.index.min().date()),
                        "end": str(df.index.max().date()),
                    },
                    "stats": {
                        "avg_close": float(df["Close"].iloc[:, 0].mean()) if "Close" in df else None,
                        "max_close": float(df["Close"].iloc[:, 0].max()) if "Close" in df else None,
                        "min_close": float(df["Close"].iloc[:, 0].min()) if "Close" in df else None,
                        "latest_close": float(df["Close"].iloc[-1, 0]) if "Close" in df else None,
                    },
                    "run_id": run_id,
                },
            )

        records = clean_vix_dataframe(df)
        dq.log_report(dq.check_price_records(records), "^VIX")

        if records:
            try:
                n = db_client.upsert_vix_data(records)
                # Publish to Kafka — fire-and-forget (Fix 15)
                if kafka_producer:
                    threading.Thread(
                        target=kafka_producer.publish_batch,
                        args=(TOPICS.get("macro", "market.macro"), records),
                        kwargs={"key_field": "cob_date"},
                        daemon=True,
                    ).start()
                if metrics:
                    metrics.record_outcome("vix", "^VIX", "SUCCESS", n)
                if progress_update:
                    progress_update("^VIX", "SUCCESS")
                db_client.insert_log(
                    _make_log_entry(
                        run_id,
                        "vix",
                        "^VIX",
                        "SUCCESS",
                        n,
                        frequency=frequency,
                        start=start_date,
                        end=end_date,
                    )
                )
                pipeline_logger.info(f"VIX: loaded {n} records")
            except Exception as e:
                if metrics:
                    metrics.record_outcome("vix", "^VIX", "FAILED")
                if progress_update:
                    progress_update("^VIX", "FAILED")
                db_client.insert_log(
                    _make_log_entry(
                        run_id, "vix", "^VIX", "FAILED", 0, str(e), frequency, start_date, end_date
                    )
                )
    else:
        if metrics:
            metrics.record_outcome("vix", "^VIX", "SKIPPED")
        if progress_update:
            progress_update("^VIX", "SKIPPED")
        pipeline_logger.warning("VIX: no data returned")

    pipeline_logger.info(f"VIX downloader stats: {downloader.stats}")
    db_client.update_pipeline_metadata("vix", last_date=end_date)
    return downloader


def _run_risk_free_rate(
    db_client,
    minio_store,
    pipeline_params,
    start_date,
    end_date,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
    kafka_producer=None,
    mongo_store=None,
):
    """Download, clean, and load daily risk-free rate data from FRED.

    Uses the 3-month US Treasury rate (DGS3MO) as the risk-free proxy
    for Sharpe ratio calculation in CW2 (Spec §7.3, Priority P2).
    """
    pipeline_logger.info("Starting risk-free rate download from FRED...")
    downloader = RiskFreeRateDownloader(
        api_delay=pipeline_params["api_delay_seconds"],
        max_retries=pipeline_params["max_retries"],
        backoff_base=pipeline_params["backoff_base"],
    )
    df = downloader.download(start_date, end_date)

    if not df.empty:
        try:
            minio_store.store_raw_csv(
                df.to_csv().encode("utf-8"), "risk_free_rate", "DGS3MO", datetime.now().strftime("%Y-%m-%d")
            )
        except Exception:
            pass

        # Store raw in MongoDB (semi-structured archive)
        if mongo_store:
            try:
                rate_col = "DGS3MO" if "DGS3MO" in df.columns else df.columns[-1]
                mongo_store.store_document(
                    "raw_macro",
                    {
                        "symbol": "DGS3MO",
                        "source": "fred",
                        "data_type": "risk_free_rate",
                        "rows": len(df),
                        "date_range": {
                            "start": str(df.iloc[0, 0]) if len(df) > 0 else None,
                            "end": str(df.iloc[-1, 0]) if len(df) > 0 else None,
                        },
                        "latest_rate": (
                            float(df[rate_col].dropna().iloc[-1]) if not df[rate_col].dropna().empty else None
                        ),
                        "run_id": run_id,
                    },
                )
            except Exception as e:
                pipeline_logger.warning(f"MongoDB archival for risk-free rate failed: {e}")

        records = clean_risk_free_rate_dataframe(df)

        if records:
            try:
                n = db_client.upsert_risk_free_rate(records)
                # Publish to Kafka — fire-and-forget (Fix 15)
                if kafka_producer:
                    threading.Thread(
                        target=kafka_producer.publish_batch,
                        args=(TOPICS.get("macro", "market.macro"), records),
                        kwargs={"key_field": "cob_date"},
                        daemon=True,
                    ).start()
                if metrics:
                    metrics.record_outcome("risk_free_rate", "DGS3MO", "SUCCESS", n)
                if progress_update:
                    progress_update("DGS3MO", "SUCCESS")
                db_client.insert_log(
                    _make_log_entry(
                        run_id,
                        "risk_free_rate",
                        "DGS3MO",
                        "SUCCESS",
                        n,
                        frequency=frequency,
                        start=start_date,
                        end=end_date,
                    )
                )
                pipeline_logger.info(f"Risk-free rate: loaded {n} records")
            except Exception as e:
                if metrics:
                    metrics.record_outcome("risk_free_rate", "DGS3MO", "FAILED")
                if progress_update:
                    progress_update("DGS3MO", "FAILED")
                db_client.insert_log(
                    _make_log_entry(
                        run_id,
                        "risk_free_rate",
                        "DGS3MO",
                        "FAILED",
                        0,
                        str(e),
                        frequency,
                        start_date,
                        end_date,
                    )
                )
    else:
        if metrics:
            metrics.record_outcome("risk_free_rate", "DGS3MO", "SKIPPED")
        if progress_update:
            progress_update("DGS3MO", "SKIPPED")
        pipeline_logger.warning("Risk-free rate: no data returned")

    pipeline_logger.info(f"Risk-free rate downloader stats: {downloader.stats}")
    db_client.update_pipeline_metadata("risk_free_rate", last_date=end_date)
    return downloader


BENCHMARK_SYMBOLS = [
    "^GSPC",  # S&P 500 (US)
    "^FTSE",  # FTSE 100 (UK)
    "^STOXX50E",  # Euro Stoxx 50 (EU)
    "^GSPTSE",  # S&P/TSX Composite (Canada)
    "^SSMI",  # SMI (Switzerland)
]


def _run_benchmark(
    db_client,
    minio_store,
    pipeline_params,
    start_date,
    end_date,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
    kafka_producer=None,
    mongo_store=None,
):
    """Download, clean, and load daily benchmark index data (S&P 500).

    Uses the same yfinance download as VIX. The S&P 500 (^GSPC) is the
    standard benchmark for relative performance and beta calculation.
    """
    import yfinance as yf

    from modules.processing.data_cleaner import clean_price_dataframe

    pipeline_logger.info("Starting benchmark index download...")
    total_loaded = 0

    for symbol in BENCHMARK_SYMBOLS:
        if _check_shutdown("benchmark"):
            break
        try:
            df = yf.download(
                symbol, start=start_date, end=end_date, progress=False, timeout=30, auto_adjust=False
            )
            if df is not None and not df.empty:
                try:
                    minio_store.store_raw_csv(
                        df.to_csv().encode("utf-8"),
                        "benchmark",
                        symbol.replace("^", ""),
                        datetime.now().strftime("%Y-%m-%d"),
                    )
                except Exception:
                    pass

                # Store raw in MongoDB (semi-structured archive)
                if mongo_store:
                    mongo_store.store_document(
                        "raw_benchmark",
                        {
                            "symbol": symbol,
                            "source": "yfinance",
                            "rows": len(df),
                            "date_range": {
                                "start": str(df.index.min().date()),
                                "end": str(df.index.max().date()),
                            },
                            "stats": {
                                "avg_close": float(df["Close"].iloc[:, 0].mean()) if "Close" in df else None,
                                "latest_close": float(df["Close"].iloc[-1, 0]) if "Close" in df else None,
                                "period_return_pct": (
                                    float((df["Close"].iloc[-1, 0] / df["Close"].iloc[0, 0] - 1) * 100)
                                    if "Close" in df and len(df) > 1
                                    else None
                                ),
                            },
                            "run_id": run_id,
                        },
                    )

                records = clean_price_dataframe(df, symbol, "USD")
                # Strip currency field — benchmark_index table has no currency column
                for r in records:
                    r.pop("currency", None)
                if records:
                    n = db_client.upsert_benchmark_index(records)
                    total_loaded += n
                    # Publish to Kafka — fire-and-forget (Fix 15)
                    if kafka_producer:
                        threading.Thread(
                            target=kafka_producer.publish_batch,
                            args=(TOPICS.get("macro", "market.macro"), records),
                            kwargs={"key_field": "symbol"},
                            daemon=True,
                        ).start()
                    if metrics:
                        metrics.record_outcome("benchmark", symbol, "SUCCESS", n)
                    if progress_update:
                        progress_update(symbol, "SUCCESS")
                    db_client.insert_log(
                        _make_log_entry(
                            run_id,
                            "benchmark",
                            symbol,
                            "SUCCESS",
                            n,
                            frequency=frequency,
                            start=start_date,
                            end=end_date,
                        )
                    )
                    pipeline_logger.info(f"Benchmark {symbol}: loaded {n} records")
        except Exception as e:
            if metrics:
                metrics.record_outcome("benchmark", symbol, "FAILED")
            if progress_update:
                progress_update(symbol, "FAILED")
            db_client.insert_log(
                _make_log_entry(
                    run_id, "benchmark", symbol, "FAILED", 0, str(e), frequency, start_date, end_date
                )
            )
            pipeline_logger.error(f"Benchmark {symbol} failed: {e}")

    pipeline_logger.info(f"Benchmark: loaded {total_loaded} records total")
    db_client.update_pipeline_metadata("benchmark", last_date=end_date)


# Financial ratios we extract from yfinance Ticker.info
RATIO_FIELDS = {
    "marketCap": "market_cap",
    "trailingPE": "pe_ratio_trailing",
    "forwardPE": "pe_ratio_forward",
    "priceToBook": "price_to_book",
    "enterpriseToEbitda": "ev_to_ebitda",
    "enterpriseValue": "enterprise_value",
    "dividendYield": "dividend_yield",
    "beta": "beta",
    "returnOnEquity": "return_on_equity",
    "debtToEquity": "debt_to_equity",
    "currentRatio": "current_ratio",
    "operatingMargins": "operating_margin",
    "profitMargins": "profit_margin",
    "revenueGrowth": "revenue_growth",
    "earningsGrowth": "earnings_growth",
    "trailingEps": "trailing_eps",
    "forwardEps": "forward_eps",
    "pegRatio": "peg_ratio",
    "shortRatio": "short_ratio",
    "fiftyTwoWeekHigh": "fifty_two_week_high",
    "fiftyTwoWeekLow": "fifty_two_week_low",
    "sharesOutstanding": "shares_outstanding",
    "floatShares": "float_shares",
    "bookValue": "book_value_per_share",
    "freeCashflow": "free_cash_flow",
    "operatingCashflow": "operating_cash_flow",
    "totalRevenue": "total_revenue_ttm",
    "grossMargins": "gross_margin",
}

# Finnhub /stock/metric fields → canonical ratio names (US tickers only on free tier)
FINNHUB_METRIC_FIELDS = {
    "marketCapitalization": "market_cap",
    "peNormalizedAnnual": "pe_ratio_trailing",
    "priceToBookAnnual": "price_to_book",
    "dividendYieldIndicatedAnnual": "dividend_yield",
    "beta": "beta",
    "roeTTM": "return_on_equity",
    "debtEquityAnnual": "debt_to_equity",
    "currentRatioAnnual": "current_ratio",
    "operatingMarginAnnual": "operating_margin",
    "netProfitMarginTTM": "profit_margin",
    "revenueGrowth3Y": "revenue_growth",
    "epsNormalizedAnnual": "trailing_eps",
    "52WeekHigh": "fifty_two_week_high",
    "52WeekLow": "fifty_two_week_low",
    "grossMarginAnnual": "gross_margin",
    "totalSharesOutstanding": "shares_outstanding",
    "bookValueShareAnnual": "book_value_per_share",
    "freeCashFlowTTM": "free_cash_flow",
    "operatingCashFlowTTM": "operating_cash_flow",
    "enterpriseValueAnnual": "enterprise_value",
    "evEbitdaAnnual": "ev_to_ebitda",
}


def _extract_ratios_from_info(info: dict, db_symbol: str) -> list[dict]:
    """Extract financial ratios from yfinance Ticker.info dict.

    :param info: Ticker.info dictionary from yfinance
    :param db_symbol: Database symbol
    :return: List of ratio record dicts for company_ratios table
    """
    from datetime import date

    import numpy as np

    if not info or not isinstance(info, dict):
        return []

    today = date.today()
    records = []

    for yf_key, canonical_name in RATIO_FIELDS.items():
        val = info.get(yf_key)
        if val is None:
            continue
        try:
            fval = float(val)
            if np.isnan(fval) or np.isinf(fval):
                continue
            records.append(
                {
                    "symbol": db_symbol,
                    "snapshot_date": today,
                    "field_name": canonical_name,
                    "field_value": fval,
                }
            )
        except (ValueError, TypeError):
            continue

    # ── Default dividend_yield to 0.0 if not present ──
    # Non-dividend payers have no dividendYield in yfinance info.
    # A yield of 0.0 is factually correct (not NULL).
    if not any(r["field_name"] == "dividend_yield" for r in records):
        records.append({
            "symbol": db_symbol,
            "snapshot_date": today,
            "field_name": "dividend_yield",
            "field_value": 0.0,
        })

    # ── Derived ratios computed from raw fields ──
    _derived = _compute_derived_ratios(info, db_symbol, today)
    records.extend(_derived)

    return records


def _compute_derived_ratios(info: dict, db_symbol: str, snapshot_date) -> list[dict]:
    """Compute value-signal and quality-signal ratios from Ticker.info.

    Value signals (Section 4.2 of the Investment Strategy Spec):
      B/P  = Book Value per Share / Price
      E/P  = EPS (TTM) / Price
      CF/P = Operating Cash Flow (TTM) / Market Cap

    Quality signals (Section 4.3):
      ROE (computed)      = Net Income / Shareholders' Equity
      D/E (inverted)      = 1 / Debt-to-Equity  (lower D/E = higher quality)

    :param info: yfinance Ticker.info dict
    :param db_symbol: Database symbol
    :param snapshot_date: date for the snapshot
    :return: List of computed ratio record dicts
    """
    import numpy as np

    records = []
    price = info.get("regularMarketPrice") or info.get("currentPrice")

    def _safe_append(field_name, value):
        try:
            fval = float(value)
            if not np.isnan(fval) and not np.isinf(fval):
                records.append(
                    {
                        "symbol": db_symbol,
                        "snapshot_date": snapshot_date,
                        "field_name": field_name,
                        "field_value": fval,
                    }
                )
        except (ValueError, TypeError):
            pass

    if price and float(price) > 0:
        p = float(price)

        # B/P = Book Value per Share / Price
        bvps = info.get("bookValue")
        if bvps is not None:
            _safe_append("book_to_price", float(bvps) / p)

        # E/P = EPS (TTM) / Price
        eps = info.get("trailingEps")
        if eps is not None:
            _safe_append("earnings_to_price", float(eps) / p)

        # CF/P = Operating Cash Flow / Market Cap
        ocf = info.get("operatingCashflow")
        mcap = info.get("marketCap")
        if ocf is not None and mcap and float(mcap) > 0:
            _safe_append("cashflow_to_price", float(ocf) / float(mcap))

    # ROE (computed) = netIncomeToCommon / shareholders' equity
    # Primary: use totalStockholderEquity if available
    # Fallback: estimate equity as bookValue * sharesOutstanding
    net_income = info.get("netIncomeToCommon")
    equity = info.get("totalStockholderEquity")
    if equity is None or float(equity or 0) == 0:
        bv = info.get("bookValue")
        shares = info.get("sharesOutstanding")
        if bv is not None and shares is not None and float(shares) > 0:
            equity = float(bv) * float(shares)
    if net_income is not None and equity and float(equity) != 0:
        _safe_append("roe_computed", float(net_income) / float(equity))

    # D/E inverted (for quality scoring — lower D/E = higher quality)
    de = info.get("debtToEquity")
    if de is not None and float(de) != 0:
        _safe_append("debt_to_equity_inv", 1.0 / float(de))

    return records


def _compute_earnings_stability(db_client, db_symbol: str, snapshot_date) -> list[dict]:
    """Compute earnings stability from historical quarterly EPS.

    Earnings Stability = 1 / std_dev(quarter-over-quarter EPS growth)
    over trailing 3 years (12 quarters).  Higher value = more stable.

    Requires at least 4 quarterly EPS observations to compute growth
    standard deviation.

    :param db_client: Database client for querying fundamentals
    :param db_symbol: Database symbol
    :param snapshot_date: date for the snapshot
    :return: List with one record dict, or empty list if not computable
    """
    import numpy as np

    try:
        from sqlalchemy import text

        query = text(
            "SELECT field_value FROM systematic_equity.fundamentals "
            "WHERE TRIM(symbol) = :sym "
            "AND field_name IN ('diluted_eps', 'basic_eps') "
            "AND period_type = 'quarterly' "
            "ORDER BY report_date DESC LIMIT 12"
        )
        with db_client.connection.connect() as conn:
            rows = conn.execute(query, {"sym": db_symbol}).fetchall()

        if len(rows) < 4:
            return []

        eps_values = [float(r[0]) for r in rows if r[0] is not None]
        if len(eps_values) < 4:
            return []

        # Quarter-over-quarter growth rates
        growths = []
        for i in range(len(eps_values) - 1):
            prev = eps_values[i + 1]  # older quarter (rows are DESC)
            curr = eps_values[i]
            if prev != 0:
                growths.append((curr - prev) / abs(prev))

        if len(growths) < 3:
            return []

        std = float(np.std(growths, ddof=1))
        if std <= 0 or np.isnan(std) or np.isinf(std):
            return []

        stability = 1.0 / std
        # Cap at a reasonable maximum to avoid extreme outliers
        stability = min(stability, 100.0)

        return [
            {
                "symbol": db_symbol,
                "snapshot_date": snapshot_date,
                "field_name": "earnings_stability",
                "field_value": round(stability, 6),
            }
        ]
    except Exception:
        return []


def _compute_debt_equity_from_fundamentals(db_client, db_symbol: str, snapshot_date) -> list[dict]:
    """Compute D/E ratio from fundamentals when yfinance debtToEquity is missing.

    Uses the most recent quarterly total_debt and stockholders_equity from
    the fundamentals table.  Returns both debt_to_equity and debt_to_equity_inv.

    :param db_client: Database client for querying fundamentals
    :param db_symbol: Database symbol
    :param snapshot_date: date for the snapshot
    :return: List of record dicts (0-2 items), empty if not computable
    """
    try:
        from sqlalchemy import text

        query = text(
            "SELECT f1.field_value AS total_debt, f2.field_value AS equity "
            "FROM systematic_equity.fundamentals f1 "
            "JOIN systematic_equity.fundamentals f2 "
            "  ON TRIM(f1.symbol) = TRIM(f2.symbol) "
            "  AND f1.report_date = f2.report_date "
            "  AND f1.period_type = f2.period_type "
            "WHERE TRIM(f1.symbol) = :sym "
            "  AND f1.field_name = 'total_debt' "
            "  AND f2.field_name = 'stockholders_equity' "
            "  AND f1.period_type = 'quarterly' "
            "  AND f2.field_value IS NOT NULL AND f2.field_value != 0 "
            "ORDER BY f1.report_date DESC LIMIT 1"
        )
        with db_client.connection.connect() as conn:
            rows = conn.execute(query, {"sym": db_symbol}).fetchall()

        if not rows:
            return []

        total_debt = float(rows[0][0])
        equity = float(rows[0][1])
        if equity == 0:
            return []

        de_ratio = total_debt / equity
        records = [
            {
                "symbol": db_symbol,
                "snapshot_date": snapshot_date,
                "field_name": "debt_to_equity",
                "field_value": round(de_ratio, 6),
            },
        ]
        if de_ratio != 0:
            records.append(
                {
                    "symbol": db_symbol,
                    "snapshot_date": snapshot_date,
                    "field_name": "debt_to_equity_inv",
                    "field_value": round(1.0 / de_ratio, 6),
                }
            )
        return records
    except Exception:
        return []


def _fetch_finnhub_metric_ratios(yf_ticker: str, api_key: str, db_symbol: str) -> list[dict]:
    """Fetch financial metrics from Finnhub for US tickers (free tier).

    Uses Finnhub's /stock/metric endpoint.  Only works for US tickers
    on the free API plan; non-US tickers return 403 Forbidden.

    :param yf_ticker: Yahoo Finance ticker (US only — no dot suffix)
    :param api_key: Finnhub API key
    :param db_symbol: Database symbol
    :return: List of ratio record dicts (empty on failure / non-US)
    """
    import json
    import urllib.error
    import urllib.request
    from datetime import date as _date

    import numpy as np

    if not api_key or "." in yf_ticker:
        return []

    url = f"https://finnhub.io/api/v1/stock/metric" f"?symbol={yf_ticker}&metric=all&token={api_key}"
    try:
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return []
        raise
    except Exception:
        return []

    metric = data.get("metric", {})
    if not metric:
        return []

    today = _date.today()
    records = []
    for fh_key, canonical in FINNHUB_METRIC_FIELDS.items():
        val = metric.get(fh_key)
        if val is None:
            continue
        try:
            fval = float(val)
            if np.isnan(fval) or np.isinf(fval):
                continue
            records.append(
                {
                    "symbol": db_symbol,
                    "snapshot_date": today,
                    "field_name": canonical,
                    "field_value": fval,
                }
            )
        except (ValueError, TypeError):
            continue

    return records


def _extract_ratios_from_fast_info(ticker_obj, db_symbol: str) -> list[dict]:
    """Extract basic ratio fields from yfinance fast_info (all tickers).

    fast_info is a lightweight endpoint that works for ALL tickers
    including non-US, even when Ticker.info fails or returns sparse data.
    Provides: market_cap, shares_outstanding, 52-week high/low.

    :param ticker_obj: yfinance Ticker object (already instantiated)
    :param db_symbol: Database symbol
    :return: List of ratio record dicts (may be empty)
    """
    from datetime import date as _date

    import numpy as np

    fast_info_map = {
        "market_cap": "market_cap",
        "shares": "shares_outstanding",
        "year_high": "fifty_two_week_high",
        "year_low": "fifty_two_week_low",
    }

    try:
        fi = ticker_obj.fast_info
    except Exception:
        return []

    today = _date.today()
    records = []
    for fi_key, canonical in fast_info_map.items():
        try:
            val = getattr(fi, fi_key, None)
            if val is None:
                continue
            fval = float(val)
            if np.isnan(fval) or np.isinf(fval) or fval == 0.0:
                continue
            records.append(
                {
                    "symbol": db_symbol,
                    "snapshot_date": today,
                    "field_name": canonical,
                    "field_value": fval,
                }
            )
        except (ValueError, TypeError, AttributeError):
            continue

    return records


def _run_ratios(
    db_client,
    minio_store,
    ticker_map,
    pipeline_params,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
    kafka_producer=None,
    mongo_store=None,
):
    """Download financial ratios and market cap from yfinance Ticker.info.

    Extracts P/E, P/B, EV/EBITDA, market cap, beta, margins, etc.
    These are point-in-time snapshots (current values, not historical).

    Downloads are parallelised via ``ThreadPoolExecutor`` — each worker
    handles a *different* symbol, so there is no same-symbol concurrent
    access (safe for yfinance ``Ticker.info`` in Python 3).
    """
    import os
    import time as _time

    import yfinance as yf
    from yfinance.exceptions import YFRateLimitError, YFTickerMissingError, YFTzMissingError

    api_delay = pipeline_params.get("api_delay_seconds", 0.5)
    max_retries = pipeline_params.get("max_retries", 3)
    backoff_base = pipeline_params.get("backoff_base", 2.0)
    workers = pipeline_params.get("ratios_workers", 8)

    pipeline_logger.info(
        f"Starting company ratios download ({workers} parallel workers, "
        f"up to {max_retries} retries per ticker)..."
    )

    total_loaded = 0
    _count_lock = threading.Lock()
    finnhub_api_key = os.environ.get("FINNHUB_API_KEY", "")

    def _process_ticker(item):
        nonlocal total_loaded
        db_symbol, yf_ticker, _currency = item

        if _check_shutdown("ratios"):
            return

        if db_symbol in _inactive_tickers:
            if metrics:
                metrics.record_outcome("ratios", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            return

        # ── Primary source: yfinance Ticker.info ──
        ticker_obj = None
        records = []
        last_exc = None

        for attempt in range(max_retries):
            try:
                ticker_obj = yf.Ticker(yf_ticker)
                info = ticker_obj.info
                records = _extract_ratios_from_info(info, db_symbol)

                # Store raw in MongoDB (semi-structured archive)
                if mongo_store and info:
                    mongo_store.store_document(
                        "raw_ratios",
                        {
                            "symbol": db_symbol,
                            "source": "yfinance",
                            "fields_extracted": len(records),
                            "field_names": [r["field_name"] for r in records],
                            "key_metrics": {
                                "market_cap": info.get("marketCap"),
                                "pe_ratio": info.get("trailingPE"),
                                "price_to_book": info.get("priceToBook"),
                                "dividend_yield": info.get("dividendYield"),
                                "beta": info.get("beta"),
                                "sector": info.get("sector", ""),
                            },
                            "run_id": run_id,
                        },
                    )

                break  # info fetched (even if 0 records extracted)

            except (YFTzMissingError, YFTickerMissingError) as e:
                # Delisted / missing tickers — no point retrying.
                # Classify as SKIPPED so they don't inflate the FAILED count.

                if metrics:
                    metrics.record_outcome("ratios", db_symbol, "SKIPPED")
                if progress_update:
                    progress_update(db_symbol, "SKIPPED")
                pipeline_logger.debug(f"Ratios SKIPPED {db_symbol} (delisted/missing): {e}")
                return
            except Exception as e:
                # HTTP 404 "Quote not found" — ticker not available via
                # quoteSummary endpoint; classify as SKIPPED, not FAILED.
                e_str = str(e)
                if "404" in e_str or "Not Found" in e_str or "not found for symbol" in e_str.lower():

                    if metrics:
                        metrics.record_outcome("ratios", db_symbol, "SKIPPED")
                    if progress_update:
                        progress_update(db_symbol, "SKIPPED")
                    pipeline_logger.debug(f"Ratios SKIPPED {db_symbol} (404 not found): {e}")
                    return
                last_exc = e
                ticker_obj = None
                if attempt < max_retries - 1:
                    # Rate-limit errors need a longer cool-down before retry.
                    wait = backoff_base**attempt
                    if isinstance(e, YFRateLimitError):
                        wait = max(wait, 30.0)
                    pipeline_logger.debug(
                        f"Ratios retry {attempt + 1}/{max_retries} "
                        f"for {db_symbol}: {e}. Waiting {wait:.1f}s"
                    )
                    _time.sleep(wait)
                else:
                    if metrics:
                        metrics.record_outcome("ratios", db_symbol, "FAILED")
                    if progress_update:
                        progress_update(db_symbol, "FAILED")
                    pipeline_logger.debug(
                        f"Ratios failed for {db_symbol} after {max_retries} " f"attempts (info): {last_exc}"
                    )
                    return

        # ── Gap-fill 1: Finnhub /stock/metric (US tickers only, free tier) ──
        if not records and finnhub_api_key and "." not in yf_ticker:
            try:
                fh_records = _fetch_finnhub_metric_ratios(yf_ticker, finnhub_api_key, db_symbol)
                if fh_records:
                    records = fh_records
            except Exception as e:
                pipeline_logger.debug(f"Finnhub metric gap-fill failed for {db_symbol}: {e}")

        # ── Gap-fill 2: yfinance fast_info (all tickers) ──
        if not records:
            try:
                if ticker_obj is None:
                    ticker_obj = yf.Ticker(yf_ticker)
                fi_records = _extract_ratios_from_fast_info(ticker_obj, db_symbol)
                if fi_records:
                    records = fi_records
            except Exception as e:
                pipeline_logger.debug(f"fast_info gap-fill failed for {db_symbol}: {e}")

        # ── Earnings Stability (cross-table: needs historical quarterly EPS) ──
        from datetime import date as _d

        es_records = _compute_earnings_stability(db_client, db_symbol, _d.today())
        records.extend(es_records)

        # ── Fundamentals-based D/E fallback ──
        # If debt_to_equity_inv was NOT computed from Ticker.info, try
        # computing it from the most recent total_debt / stockholders_equity
        # in the fundamentals table.
        has_de = any(r.get("field_name") == "debt_to_equity_inv" for r in records)
        if not has_de:
            de_records = _compute_debt_equity_from_fundamentals(db_client, db_symbol, _d.today())
            records.extend(de_records)

        if records:
            n = db_client.upsert_company_ratios(records)
            with _count_lock:
                total_loaded += n
            # Publish to Kafka — fire-and-forget daemon thread (Fix 15 pattern).
            # kafka_producer.flush(timeout=10) blocks up to 10s per ticker;
            # daemon thread prevents exhausting the PostgreSQL connection pool.
            if kafka_producer:
                threading.Thread(
                    target=kafka_producer.publish_batch,
                    args=(
                        TOPICS.get("fundamentals", "market.fundamentals"),
                        records,
                    ),
                    kwargs={"key_field": "symbol"},
                    daemon=True,
                ).start()
            if metrics:
                metrics.record_outcome("ratios", db_symbol, "SUCCESS", n)
            if progress_update:
                progress_update(db_symbol, "SUCCESS")
            db_client.insert_log(
                _make_log_entry(run_id, "ratios", db_symbol, "SUCCESS", n, frequency=frequency)
            )
        else:
            if metrics:
                metrics.record_outcome("ratios", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")

        _time.sleep(api_delay)

    # ── Crumb warm-up: refresh yfinance session before parallel workers start ──
    # The prices phase may have poisoned the shared crumb via delisted tickers
    # (HTTP 401 Invalid Crumb errors). A single warm-up call with a known
    # stable ticker forces yfinance to obtain a fresh valid crumb.
    try:
        _warmup = yf.Ticker("AAPL")
        _ = _warmup.info.get("regularMarketPrice")
        pipeline_logger.info("Ratios: yfinance crumb warm-up OK")
    except Exception as _e:
        pipeline_logger.warning(f"Ratios: crumb warm-up failed ({_e}) — workers will retry")

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ratios-worker")
    ratio_futures = [pool.submit(_process_ticker, t) for t in ticker_map]
    done, pending = futures_wait(ratio_futures, timeout=180)
    if pending:
        pipeline_logger.warning(
            f"Ratios: {len(pending)} workers still running after 180s timeout "
            f"— continuing (Fix 15 pattern)"
        )
    pool.shutdown(wait=False)

    pipeline_logger.info(f"Ratios: loaded {total_loaded} records total")
    db_client.update_pipeline_metadata("ratios")


def _run_esg(
    db_client,
    mongo_store,
    kafka_producer,
    ticker_map,
    pipeline_params,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
):
    """Download ESG sustainability scores from yfinance.

    Stores results in PostgreSQL (esg_scores table) for analytical
    queries, in MongoDB (esg_reports collection) for raw document
    storage, and publishes events to Kafka (esg.scores topic).

    :param db_client: PostgreSQL database client
    :param mongo_store: MongoDB document store
    :param kafka_producer: Kafka producer client
    :param ticker_map: List of (db_symbol, yf_ticker, currency) tuples
    :param pipeline_params: Pipeline configuration parameters
    :param run_id: Unique pipeline run identifier
    :param frequency: Pipeline run frequency
    :param metrics: Pipeline metrics collector
    :param progress_update: Progress callback function
    :return: EsgDownloader instance with statistics
    :rtype: EsgDownloader
    """
    import time as _time

    api_delay = pipeline_params.get("api_delay_seconds", 0.5)
    max_retries = pipeline_params.get("max_retries", 3)
    backoff_base = pipeline_params.get("backoff_base", 2.0)

    downloader = EsgDownloader(
        api_delay=api_delay,
        max_retries=max_retries,
        backoff_base=backoff_base,
    )

    pipeline_logger.info("Starting ESG scores download...")
    total_loaded = 0

    # ── Batch LSEG: single API call for all tickers (~N× faster) ──
    # download_batch() fetches the entire universe in one lseg.data.get_data()
    # call, eliminating per-ticker api_delay stalls.  Falls back to the
    # per-ticker path automatically if LSEG is unconfigured or the call fails.
    batch_results = downloader.download_batch(ticker_map)
    use_batch = bool(batch_results)
    if use_batch:
        pipeline_logger.info(
            f"ESG: using batch results " f"({len(batch_results)} tickers pre-fetched from LSEG)"
        )

    for db_symbol, yf_ticker, currency in ticker_map:
        if _check_shutdown("esg"):
            break

        if db_symbol in _inactive_tickers:
            if metrics:
                metrics.record_outcome("esg", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            continue

        try:
            if use_batch:
                # Pre-fetched — no per-ticker API call needed
                raw_record = batch_results.get(yf_ticker)
                downloader._download_count += 1
                if raw_record is not None:
                    downloader._success_count += 1
            else:
                raw_record = downloader.download(yf_ticker)

            # Store raw response in MongoDB (semi-structured archive)
            if raw_record and mongo_store:
                mongo_store.store_document(
                    "esg_reports",
                    {
                        "symbol": db_symbol,
                        "source": raw_record.get("source", "unknown"),
                        "data": raw_record,
                        "total_esg": raw_record.get("total_esg"),
                        "environment_score": raw_record.get("environment_score"),
                        "social_score": raw_record.get("social_score"),
                        "governance_score": raw_record.get("governance_score"),
                        "peer_group": raw_record.get("peer_group", ""),
                        "run_id": run_id,
                    },
                )

            # Publish to Kafka — fire-and-forget (Fix 15)
            if raw_record and kafka_producer:
                threading.Thread(
                    target=kafka_producer.publish,
                    args=(TOPICS.get("esg", "esg.scores"), db_symbol, raw_record),
                    daemon=True,
                ).start()

            # Clean and upsert to PostgreSQL
            cleaned = clean_esg_record(raw_record)
            if cleaned:
                cleaned["symbol"] = db_symbol
                n = db_client.upsert_esg_scores([cleaned])
                total_loaded += n
                if metrics:
                    metrics.record_outcome("esg", db_symbol, "SUCCESS", n)
                if progress_update:
                    progress_update(db_symbol, "SUCCESS")
                db_client.insert_log(
                    _make_log_entry(run_id, "esg", db_symbol, "SUCCESS", n, frequency=frequency)
                )
            else:
                if metrics:
                    metrics.record_outcome("esg", db_symbol, "SKIPPED")
                if progress_update:
                    progress_update(db_symbol, "SKIPPED")

            # Only sleep between API calls on the per-ticker yfinance path
            if not use_batch:
                _time.sleep(api_delay)
        except Exception as e:
            if metrics:
                metrics.record_outcome("esg", db_symbol, "FAILED")
            if progress_update:
                progress_update(db_symbol, "FAILED")
            pipeline_logger.debug(f"ESG failed for {db_symbol}: {e}")

    pipeline_logger.info(f"ESG: loaded {total_loaded} records total")
    db_client.update_pipeline_metadata("esg")

    # Close the LSEG session to release the server-side signon slot.
    # signon_control=True allows only 1 session per user — if the session
    # is not closed, the next pipeline run may fail to open a new one.
    try:
        import lseg.data as ld
        ld.close_session()
        pipeline_logger.info("LSEG session closed")
    except Exception:
        pass  # May not have been opened (ImportError, credentials missing, etc.)

    return downloader


def _run_news_sentiment(
    db_client,
    mongo_store,
    kafka_producer,
    minio_store,
    ticker_map,
    pipeline_params,
    run_id,
    frequency,
    metrics=None,
    progress_update=None,
):
    """Download news articles, score sentiment, and store results.

    Data flow for each ticker:
      1. yfinance Ticker.news → raw article list
      2. Parse articles → standardised records
      3. Store raw articles in MongoDB (news_sentiment collection)
      4. Score headlines with keyword-based sentiment
      5. Aggregate scores → upsert to PostgreSQL (news_sentiment table)
      6. Publish scored events to Kafka (market.sentiment topic)
      7. Backup raw JSON to MinIO

    :param db_client: PostgreSQL database client
    :param mongo_store: MongoDB document store
    :param kafka_producer: Kafka producer client
    :param minio_store: MinIO object store
    :param ticker_map: List of (db_symbol, yf_ticker, currency) tuples
    :param pipeline_params: Pipeline configuration parameters
    :param run_id: Unique pipeline run identifier
    :param frequency: Pipeline run frequency
    :param metrics: Pipeline metrics collector
    :param progress_update: Progress callback function
    :return: NewsDownloader instance with statistics
    :rtype: NewsDownloader
    """
    import json as _json
    from datetime import date as _date

    api_delay = pipeline_params.get("api_delay_seconds", 0.5)
    max_retries = pipeline_params.get("max_retries", 3)
    backoff_base = pipeline_params.get("backoff_base", 2.0)

    downloader = NewsDownloader(
        api_delay=api_delay,
        max_retries=max_retries,
        backoff_base=backoff_base,
    )
    gdelt = GdeltDownloader(
        api_delay=0.3,  # 3x faster: handles gap-fill burst across 6 workers
        max_retries=2,
        backoff_base=2.0,
        max_articles=10,
        timeout=15,
    )
    newsapi = NewsApiDownloader(
        api_delay=1.0,  # Free tier: 100 req/day — conservative pacing
        max_retries=2,
        backoff_base=2.0,
        max_articles=10,
        timeout=15,
    )
    newsapi_available = bool(newsapi.api_key)

    pipeline_logger.info(
        "Starting news sentiment download "
        f"(yfinance primary → {'NewsAPI → ' if newsapi_available else ''}GDELT gap-fill) "
        "— parallel..."
    )
    sentiment_workers = pipeline_params.get("sentiment_workers", 6)
    total_loaded = 0
    _total_lock = threading.Lock()
    today = _date.today().isoformat()

    def _process_ticker_sentiment(item):
        """Download, score, and store sentiment for one ticker (thread-safe).

        yfinance Ticker.news is thread-safe for different symbols.
        GDELT uses requests (thread-safe). DB writes are protected by
        the db_client's own connection-pool locking.
        """
        nonlocal total_loaded
        db_symbol, yf_ticker, currency = item
        if _check_shutdown("sentiment"):
            return

        if db_symbol in _inactive_tickers:
            if metrics:
                metrics.record_outcome("sentiment", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            return

        try:
            # ── Source 1: yfinance Ticker.news (primary) ──
            raw_articles = downloader.download(yf_ticker)
            yf_parsed = parse_news_articles(raw_articles, db_symbol) if raw_articles else []

            # ── Source 2: NewsAPI (secondary — gap-fill when yfinance has 0) ──
            newsapi_articles = []
            newsapi_parsed = []
            if not yf_parsed and newsapi_available:
                newsapi_articles = newsapi.download(db_symbol)
                newsapi_parsed = (
                    parse_newsapi_articles(newsapi_articles, db_symbol) if newsapi_articles else []
                )

            # ── Source 3: GDELT DOC API (tertiary — only if both above returned 0) ──
            gdelt_articles = []
            gdelt_parsed = []
            if not yf_parsed and not newsapi_parsed:
                gdelt_articles = gdelt.download(db_symbol)
                gdelt_parsed = parse_gdelt_articles(gdelt_articles, db_symbol) if gdelt_articles else []

            # ── Merge articles from all sources ──
            parsed = yf_parsed + newsapi_parsed + gdelt_parsed

            if not parsed:
                if metrics:
                    metrics.record_outcome("sentiment", db_symbol, "SKIPPED")
                if progress_update:
                    progress_update(db_symbol, "SKIPPED")
                return

            # 1. Store raw articles in MongoDB (semi-structured archive)
            if mongo_store:
                docs = []
                for art in yf_parsed:
                    docs.append(
                        {
                            "symbol": db_symbol,
                            "source": "yfinance_news",
                            "headline": art.get("title", ""),
                            "publisher": art.get("publisher", ""),
                            "published_at": art.get("published_at", ""),
                            "article": art,
                            "run_id": run_id,
                        }
                    )
                for art in newsapi_parsed:
                    docs.append(
                        {
                            "symbol": db_symbol,
                            "source": "newsapi",
                            "headline": art.get("title", ""),
                            "publisher": art.get("publisher", ""),
                            "published_at": art.get("published_at", ""),
                            "article": art,
                            "run_id": run_id,
                        }
                    )
                for art in gdelt_parsed:
                    docs.append(
                        {
                            "symbol": db_symbol,
                            "source": "gdelt",
                            "headline": art.get("title", ""),
                            "publisher": art.get("publisher", ""),
                            "published_at": art.get("published_at", ""),
                            "source_country": art.get("source_country", ""),
                            "article": art,
                            "run_id": run_id,
                        }
                    )
                if docs:
                    mongo_store.store_documents("news_sentiment", docs)

            # 2. Store raw JSON in MinIO
            try:
                combined = {
                    "yfinance": raw_articles or [],
                    "newsapi": newsapi_articles or [],
                    "gdelt": gdelt_articles or [],
                }
                raw_json = _json.dumps(combined, default=str).encode("utf-8")
                minio_store.store_raw_json(raw_json, "news_sentiment", db_symbol, today)
            except Exception:
                pass  # MinIO is non-critical

            # 3. Deduplicate headlines (same story syndicated across outlets)
            #    then score with VADER + financial domain boost
            parsed = deduplicate_articles(parsed)
            scored = score_articles(parsed)

            # 4. Aggregate and upsert to PostgreSQL
            agg = aggregate_sentiment(scored, db_symbol)
            if agg:
                agg["cob_date"] = today
                n = db_client.upsert_news_sentiment([agg])
                with _total_lock:
                    total_loaded += n
                if metrics:
                    metrics.record_outcome("sentiment", db_symbol, "SUCCESS", n)
                if progress_update:
                    progress_update(db_symbol, "SUCCESS")
                db_client.insert_log(
                    _make_log_entry(run_id, "sentiment", db_symbol, "SUCCESS", n, frequency=frequency)
                )

            # 5. Publish to Kafka — fire-and-forget daemon thread (Fix 15 pattern).
            # Prevents kafka flush from blocking the sentiment worker threads.
            if kafka_producer and scored:
                threading.Thread(
                    target=kafka_producer.publish_batch,
                    args=(
                        TOPICS.get("sentiment", "market.sentiment"),
                        scored,
                    ),
                    kwargs={"key_field": "symbol"},
                    daemon=True,
                ).start()

        except Exception as e:
            if metrics:
                metrics.record_outcome("sentiment", db_symbol, "FAILED")
            if progress_update:
                progress_update(db_symbol, "FAILED")
            pipeline_logger.debug(f"News sentiment failed for {db_symbol}: {e}")

    with ThreadPoolExecutor(max_workers=sentiment_workers, thread_name_prefix="sentiment-worker") as executor:
        list(executor.map(_process_ticker_sentiment, ticker_map))

    pipeline_logger.info(f"News sentiment: loaded {total_loaded} records total")
    db_client.update_pipeline_metadata("sentiment")
    return downloader


def main():
    """Main entry point for the Systematic Equity data pipeline.

    Orchestration flow:
    1. Register signal handlers for graceful shutdown
    2. Parse CLI args and load conf.yaml via ift_global.ReadConfig
    3. Set env variables via ift_global.set_env_variables
    4. Optionally initialise schema + load company_static
    5. Run pre-flight health checks on all dependencies
    6. Download, clean, and load: prices → fundamentals → FX → VIX
    7. Display animated progress with circuit breaker status
    8. Log all outcomes for audit trail
    9. Print rich summary table with downloader statistics
    """
    # ── 0. Register signal handlers for graceful shutdown ──
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    pipeline_logger.info("=" * 60)
    pipeline_logger.info("Systematic Equity Pipeline started")
    pipeline_logger.info("=" * 60)

    # ── 1. Parse CLI args ──
    args = arg_parse_cmd()
    parsed_args = args.parse_args()
    pipeline_logger.info(
        f"env_type={parsed_args.env_type}, "
        f"date_run={parsed_args.date_run}, "
        f"frequency={parsed_args.frequency}"
    )

    # ── 2. Load config via ift_global ──
    conf = ReadConfig(parsed_args.env_type, config_path="./config/conf.yaml")
    set_env_variables(
        env_variables=conf["config"]["env_variables"], env_type=parsed_args.env_type, env_file=True
    )
    pipeline_logger.info("Configuration loaded and environment set")

    # ── 3. Generate unique run ID ──
    run_id = generate_run_id()
    pipeline_logger.info(f"Run ID: {run_id}")

    # ── 4. Initialise database client ──
    db_client = _get_db_client(conf)
    pipeline_logger.info("Database connection established")

    # ── 5. Schema initialisation ──
    if parsed_args.init_schema:
        db_client.init_schema("./static/schema/create_tables.sql")
        pipeline_logger.info("Schema initialised")

    # ── 6. Dry run validation ──
    if parsed_args.dry_run:
        pipeline_logger.info("Dry run complete - configuration is valid")
        db_client.close()
        return

    # ── 6b. Scheduled mode (APScheduler) ──
    if getattr(parsed_args, "schedule", False):
        scheduler = PipelineScheduler(
            frequency=parsed_args.frequency,
            timezone="UTC",
        )
        if scheduler.is_available:
            # Schedule the pipeline main function for recurring execution
            scheduled = scheduler.schedule(main, job_id="cw1_pipeline")
            if scheduled:
                scheduler.start()
                next_run = scheduler.get_next_run()
                pipeline_logger.info(
                    f"Pipeline scheduled ({parsed_args.frequency}). "
                    f"Next run: {next_run}. Press Ctrl+C to stop."
                )
                try:
                    import time

                    while True:
                        time.sleep(60)
                except (KeyboardInterrupt, SystemExit):
                    scheduler.stop()
                    pipeline_logger.info("Scheduler stopped by user")
                db_client.close()
                return
        else:
            pipeline_logger.warning(
                "APScheduler not available — running once instead. " "Install with: poetry add apscheduler"
            )

    # ── 7. Calculate date range ──
    start_date, end_date = _get_date_range(conf, parsed_args)
    pipeline_logger.info(f"Date range: {start_date} to {end_date}")

    # ── 8. Load investable universe ──
    if parsed_args.tickers:
        raw_tickers = parsed_args.tickers
    else:
        raw_tickers = get_ticker_list(database=conf["config"]["Database"]["Postgres"].get("Database", "fift"))
    pipeline_logger.info(f"Processing {len(raw_tickers)} tickers")

    # ── 9. Prepare tickers: clean → infer currency → remap Swiss ──
    currency_map = conf["params"].get("CurrencyMapping", {})
    ticker_map = [prepare_yfinance_ticker(t, currency_map) for t in raw_tickers]
    pipeline_logger.info(f"Ticker preparation complete ({len(ticker_map)} tickers)")

    # ── 9b. Purge orphan prices from previous runs ──
    db_client.purge_orphan_prices()

    # ── 10. Pipeline parameters ──
    pipeline_params = conf["params"]["Pipeline"]

    # ── 11. MinIO store ──
    minio_conf = conf["config"]["Database"].get("Minio", {})
    minio_store = MinioStore(
        bucket_name=minio_conf.get("BucketName", "iftbigdata"),
        raw_data_path=minio_conf.get("RawDataPath", "raw-data"),
    )

    # ── 11b. MongoDB document store ──
    mongo_conf = conf["config"]["Database"].get("MongoDB", {})
    mongo_store = MongoDBStore(
        host=mongo_conf.get("Host", "localhost"),
        port=int(mongo_conf.get("Port", 27017)),
        username=mongo_conf.get("Username", "ift_bigdata"),
        password=mongo_conf.get("Password", "mongo_password"),
        database=mongo_conf.get("Database", "ift_cw1"),
    )

    # ── 11c. Kafka producer ──
    kafka_conf = conf["config"]["Database"].get("Kafka", {})
    kafka_producer = KafkaProducerClient(
        bootstrap_servers=kafka_conf.get("BootstrapServers", "localhost:9092"),
    )

    # ── 12. Initialise metrics collector + progress tracker ──
    metrics = PipelineMetrics(run_id)
    tracker = PipelineProgressTracker(run_id, total_tickers=len(ticker_map))
    tracker.print_banner()

    # ── 13. Pre-flight health checks ──
    pipeline_logger.info("Running pre-flight health checks...")
    health_ok = _run_health_checks(
        db_client, minio_store, conf, tracker, mongo_store=mongo_store, kafka_producer=kafka_producer
    )
    if not health_ok:
        pipeline_logger.error("Critical health checks failed — aborting")
        db_client.close()
        sys.exit(1)

    # ── 14. Run selected data sources with parallel source orchestration ──
    #
    # Parallelism strategy (3 tiers):
    #   Tier 1 — Source-level: independent sources run concurrently
    #            Group A: prices + fundamentals (share ticker universe,
    #                     but use different API endpoints and DB tables)
    #            Group B: FX + VIX (independent market data, run together)
    #   Tier 2 — Ticker-level: within each source, tickers/pairs download
    #            concurrently via ConcurrentDownloadExecutor
    #   Tier 3 — Post-processing: MinIO + cleaning + DB upsert per ticker
    #            runs in parallel threads after each batch download
    #
    sources = parsed_args.sources
    freq = parsed_args.frequency
    circuit_breakers = []
    downloaders = []
    _cb_lock = threading.Lock()
    _dl_lock = threading.Lock()

    def _append_results(dl):
        """Thread-safe append to circuit_breakers and downloaders."""
        with _cb_lock:
            circuit_breakers.append(dl.circuit_breaker)
        with _dl_lock:
            downloaders.append(dl)

    # ── Group A: prices + fundamentals (ticker-level sources) ──
    group_a_threads = []

    if "prices" in sources and not _check_shutdown("prices"):

        def _run_prices_phase():
            tracker.print_phase_start("prices")
            with metrics.track("prices"):
                with tracker.source_progress("prices", len(ticker_map)) as update:
                    dl = _run_prices(
                        db_client,
                        minio_store,
                        ticker_map,
                        pipeline_params,
                        start_date,
                        end_date,
                        run_id,
                        freq,
                        metrics,
                        update,
                        kafka_producer=kafka_producer,
                        mongo_store=mongo_store,
                    )
            _append_results(dl)
            tracker.print_phase_complete(
                "prices", metrics._timings.get("prices", 0), metrics._counts["prices"]["total_rows"]
            )

        t = threading.Thread(target=_run_prices_phase, name="source-prices")
        group_a_threads.append(t)

    if "fundamentals" in sources and not _check_shutdown("fundamentals"):

        def _run_fundamentals_phase():
            tracker.print_phase_start("fundamentals")
            with metrics.track("fundamentals"):
                with tracker.source_progress("fundamentals", len(ticker_map)) as update:
                    dls = _run_fundamentals(
                        db_client,
                        minio_store,
                        ticker_map,
                        pipeline_params,
                        run_id,
                        freq,
                        metrics,
                        update,
                        kafka_producer=kafka_producer,
                        mongo_store=mongo_store,
                    )
            for dl in dls:
                _append_results(dl)
            tracker.print_phase_complete(
                "fundamentals",
                metrics._timings.get("fundamentals", 0),
                metrics._counts["fundamentals"]["total_rows"],
            )

        t = threading.Thread(target=_run_fundamentals_phase, name="source-fundamentals")
        group_a_threads.append(t)

    # ── Build independent source threads ──
    # FX, RFR, ESG and Sentiment are fully independent of ticker-level
    # fundamentals. We create them here and start them alongside Group A
    # so they run at t=0 rather than waiting until after EDGAR finishes.
    # Each source uses its own tracker.source_progress context so the
    # progress bars appear and update correctly in parallel.
    group_independent_threads = []

    if "fx" in sources and not _check_shutdown("fx"):

        def _run_fx_phase():
            tracker.print_phase_start("fx")
            with metrics.track("fx"):
                with tracker.source_progress("fx", len(FX_PAIRS)) as update:
                    dl = _run_fx(
                        db_client,
                        minio_store,
                        pipeline_params,
                        start_date,
                        end_date,
                        run_id,
                        freq,
                        metrics,
                        update,
                        kafka_producer=kafka_producer,
                        mongo_store=mongo_store,
                    )
            _append_results(dl)
            tracker.print_phase_complete(
                "fx", metrics._timings.get("fx", 0), metrics._counts["fx"]["total_rows"]
            )

        group_independent_threads.append(threading.Thread(target=_run_fx_phase, name="source-fx"))

    if "risk_free_rate" in sources and not _check_shutdown("risk_free_rate"):

        def _run_rfr_phase():
            tracker.print_phase_start("risk_free_rate")
            with metrics.track("risk_free_rate"):
                with tracker.source_progress("risk_free_rate", 1) as update:
                    dl = _run_risk_free_rate(
                        db_client,
                        minio_store,
                        pipeline_params,
                        start_date,
                        end_date,
                        run_id,
                        freq,
                        metrics,
                        update,
                        kafka_producer=kafka_producer,
                        mongo_store=mongo_store,
                    )
            _append_results(dl)
            tracker.print_phase_complete(
                "risk_free_rate",
                metrics._timings.get("risk_free_rate", 0),
                metrics._counts["risk_free_rate"]["total_rows"],
            )

        group_independent_threads.append(
            threading.Thread(target=_run_rfr_phase, name="source-risk-free-rate")
        )

    if "esg" in sources and not _check_shutdown("esg"):

        def _run_esg_phase():
            tracker.print_phase_start("esg")
            with metrics.track("esg"):
                with tracker.source_progress("esg", len(ticker_map)) as update:
                    esg_dl = _run_esg(
                        db_client,
                        mongo_store,
                        kafka_producer,
                        ticker_map,
                        pipeline_params,
                        run_id,
                        freq,
                        metrics,
                        update,
                    )
            _append_results(esg_dl)
            tracker.print_phase_complete(
                "esg", metrics._timings.get("esg", 0), metrics._counts.get("esg", {}).get("total_rows", 0)
            )

        group_independent_threads.append(threading.Thread(target=_run_esg_phase, name="source-esg"))

    if "sentiment" in sources and not _check_shutdown("sentiment"):

        def _run_sentiment_phase():
            tracker.print_phase_start("sentiment")
            with metrics.track("sentiment"):
                with tracker.source_progress("sentiment", len(ticker_map)) as update:
                    sentiment_dl = _run_news_sentiment(
                        db_client,
                        mongo_store,
                        kafka_producer,
                        minio_store,
                        ticker_map,
                        pipeline_params,
                        run_id,
                        freq,
                        metrics,
                        update,
                    )
            _append_results(sentiment_dl)
            tracker.print_phase_complete(
                "sentiment",
                metrics._timings.get("sentiment", 0),
                metrics._counts.get("sentiment", {}).get("total_rows", 0),
            )

        group_independent_threads.append(
            threading.Thread(target=_run_sentiment_phase, name="source-sentiment")
        )

    # Launch Group A + all independent sources at t=0
    _first_wave = group_a_threads + group_independent_threads
    if _first_wave:
        _active_all = [t.name.replace("source-", "") for t in _first_wave]
        tracker.print_parallel_group_start("Group A · all independent sources", _active_all, len(_first_wave))
        for t in _first_wave:
            t.start()
        # Wait only for ticker-level sources (prices + fundamentals) —
        # EDGAR and Finnhub need the fundamentals in the DB before starting.
        # FX, RFR, ESG and Sentiment continue running in the background.
        # 2400s = 40min hard cap; each phase has internal per-batch timeouts.
        for t in group_a_threads:
            t.join(timeout=2400)
            if t.is_alive():
                pipeline_logger.warning(
                    f"Phase thread {t.name} still alive after 40min — " f"proceeding to EDGAR/Finnhub anyway"
                )

    # ── Pre-flight delisted detection (after prices phase) ──
    # Uses multi-signal analysis (stale prices + ingestion log) confirmed
    # by live yfinance fast_info checks.  Populates _inactive_tickers so
    # later phases (fundamentals, EDGAR, ratios, ESG, sentiment) skip them.
    global _inactive_tickers
    if not _check_shutdown("delisted_detection"):
        _inactive_tickers = _detect_inactive_tickers(db_client, ticker_map)

    # ── Group A.5+A.6: EDGAR + Finnhub fundamentals (parallel) ──
    #
    # EDGAR processes US tickers via SEC XBRL, Finnhub processes non-US
    # tickers via Finnhub API. They operate on disjoint ticker sets and
    # hit different external APIs, so they can safely run concurrently.
    #
    supplement_threads = []

    if "fundamentals" in sources and not _check_shutdown("edgar_fundamentals"):
        us_count = sum(1 for db, yf, cur in ticker_map if is_us_ticker(db))
        if us_count > 0:

            def _run_edgar_phase():
                pipeline_logger.info(
                    f"Running EDGAR supplementary fundamentals for " f"{us_count} US tickers..."
                )
                # EDGAR uses full lookback_years regardless of frequency,
                # because SEC 10-Q/10-K filings are quarterly/annual —
                # a frequency-based 5-day window would return 0 records.
                edgar_lookback = pipeline_params.get("lookback_years", 6)
                edgar_start = (
                    datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=365 * edgar_lookback)
                ).strftime("%Y-%m-%d")
                tracker.print_phase_start("edgar_fundamentals")
                with metrics.track("edgar_fundamentals"):
                    with tracker.source_progress("edgar_fundamentals", us_count) as update:
                        edgar_dl = _run_edgar_fundamentals(
                            db_client,
                            minio_store,
                            ticker_map,
                            pipeline_params,
                            edgar_start,
                            run_id,
                            freq,
                            metrics,
                            update,
                            kafka_producer=kafka_producer,
                            mongo_store=mongo_store,
                        )
                if edgar_dl:
                    _append_results(edgar_dl)
                    tracker.print_phase_complete(
                        "edgar_fundamentals",
                        metrics._timings.get("edgar_fundamentals", 0),
                        metrics._counts.get("edgar_fundamentals", {}).get("total_rows", 0),
                    )

            t = threading.Thread(target=_run_edgar_phase, name="source-edgar")
            supplement_threads.append(t)

    if "fundamentals" in sources and not _check_shutdown("finnhub_fundamentals"):
        non_us_count = sum(1 for db, yf, cur in ticker_map if is_non_us_ticker(db))
        if non_us_count > 0:

            def _run_finnhub_phase():
                pipeline_logger.info(
                    f"Running Finnhub supplementary fundamentals for " f"{non_us_count} non-US tickers..."
                )
                tracker.print_phase_start("finnhub_fundamentals")
                with metrics.track("finnhub_fundamentals"):
                    with tracker.source_progress("finnhub_fundamentals", non_us_count) as update:
                        finnhub_dl = _run_finnhub_fundamentals(
                            db_client,
                            minio_store,
                            ticker_map,
                            pipeline_params,
                            start_date,
                            run_id,
                            freq,
                            conf,
                            metrics,
                            update,
                            kafka_producer=kafka_producer,
                            mongo_store=mongo_store,
                        )
                if finnhub_dl:
                    _append_results(finnhub_dl)
                    tracker.print_phase_complete(
                        "finnhub_fundamentals",
                        metrics._timings.get("finnhub_fundamentals", 0),
                        metrics._counts.get("finnhub_fundamentals", {}).get("total_rows", 0),
                    )

            t = threading.Thread(target=_run_finnhub_phase, name="source-finnhub")
            supplement_threads.append(t)

    # ── Group A.7: Non-US fundamentals supplement (FMP + SimFin + AV) ──
    non_us_count_supp = sum(1 for db, yf, cur in ticker_map if is_non_us_ticker(db))
    if "fundamentals" in sources and non_us_count_supp > 0 and not _check_shutdown("nonus_supplement"):

        def _run_nonus_supp_phase():
            pipeline_logger.info(
                f"Running non-US fundamentals supplement (FMP/SimFin/AV) "
                f"for {non_us_count_supp} tickers..."
            )
            tracker.print_phase_start("nonus_supplement")
            with metrics.track("nonus_supplement"):
                with tracker.source_progress("nonus_supplement", non_us_count_supp) as update:
                    supp_dls = _run_nonus_fundamentals_supplement(
                        db_client, minio_store, ticker_map, pipeline_params,
                        start_date, run_id, freq, conf, metrics, update,
                        kafka_producer=kafka_producer, mongo_store=mongo_store,
                    )
            if supp_dls:
                for d in supp_dls:
                    _append_results(d)
                tracker.print_phase_complete(
                    "nonus_supplement",
                    metrics._timings.get("nonus_supplement", 0),
                    metrics._counts.get("nonus_supplement", {}).get("total_rows", 0),
                )

        t = threading.Thread(target=_run_nonus_supp_phase, name="source-nonus-supp")
        supplement_threads.append(t)

    # Launch EDGAR + Finnhub + Non-US supplement concurrently (disjoint ticker sets)
    if supplement_threads:
        _active_supp = [t.name.replace("source-", "") for t in supplement_threads]
        tracker.print_parallel_group_start(
            "Group A.5+A.6 · fundamentals supplements", _active_supp, len(supplement_threads)
        )
        for t in supplement_threads:
            t.start()
        # 600s = 10min hard cap; each phase has internal futures_wait(120s).
        for t in supplement_threads:
            t.join(timeout=600)
            if t.is_alive():
                pipeline_logger.warning(
                    f"Supplement thread {t.name} still alive after 10min — " f"proceeding anyway"
                )

    # ── Fundamentals post-processing: derive missing fields ──
    # Runs after ALL fundamentals sources complete (yfinance, EDGAR, Finnhub, FMP/SimFin/AV).
    # Fills gaps that exist because yfinance doesn't report certain fields historically.
    if not _check_shutdown("fundamentals_derive"):
        try:
            from sqlalchemy import text

            # 1. book_value: fill from stockholders_equity where missing
            #    (they are the same metric — total equity attributable to shareholders)
            derived_bv = db_client.read_query(
                "SELECT COUNT(*) FROM systematic_equity.fundamentals WHERE field_name = 'book_value'"
            )
            bv_before = derived_bv[0][0] if derived_bv else 0

            with db_client._engine.connect() as conn:
                conn.execute(
                    text(
                        "INSERT INTO systematic_equity.fundamentals "
                        "  (symbol, report_date, field_name, field_value, period_type, currency, ingestion_timestamp) "
                        "SELECT symbol, report_date, 'book_value', field_value, period_type, currency, NOW() "
                        "FROM systematic_equity.fundamentals "
                        "WHERE field_name = 'stockholders_equity' "
                        "  AND field_value IS NOT NULL "
                        "ON CONFLICT (symbol, report_date, field_name, period_type) DO NOTHING"
                    )
                )
                conn.commit()

            derived_bv_after = db_client.read_query(
                "SELECT COUNT(*) FROM systematic_equity.fundamentals WHERE field_name = 'book_value'"
            )
            bv_after = derived_bv_after[0][0] if derived_bv_after else 0
            if bv_after > bv_before:
                pipeline_logger.info(
                    f"Derived book_value from stockholders_equity: "
                    f"{bv_after - bv_before} new records ({bv_before} → {bv_after})"
                )

            # 2. book_value_per_share: derive from stockholders_equity / shares_outstanding
            #    for historical periods where it's missing (currently snapshot-only)
            bvps_before_q = db_client.read_query(
                "SELECT COUNT(*) FROM systematic_equity.fundamentals WHERE field_name = 'book_value_per_share'"
            )
            bvps_before = bvps_before_q[0][0] if bvps_before_q else 0

            with db_client._engine.connect() as conn:
                conn.execute(
                    text(
                        "INSERT INTO systematic_equity.fundamentals "
                        "  (symbol, report_date, field_name, field_value, period_type, currency, ingestion_timestamp) "
                        "SELECT f.symbol, f.report_date, 'book_value_per_share', "
                        "       f.field_value / cr.field_value, f.period_type, f.currency, NOW() "
                        "FROM systematic_equity.fundamentals f "
                        "JOIN systematic_equity.company_ratios cr "
                        "  ON TRIM(cr.symbol) = TRIM(f.symbol) AND cr.field_name = 'shares_outstanding' "
                        "WHERE f.field_name = 'stockholders_equity' "
                        "  AND f.field_value IS NOT NULL "
                        "  AND cr.field_value IS NOT NULL AND cr.field_value > 0 "
                        "ON CONFLICT (symbol, report_date, field_name, period_type) DO NOTHING"
                    )
                )
                conn.commit()

            bvps_after_q = db_client.read_query(
                "SELECT COUNT(*) FROM systematic_equity.fundamentals WHERE field_name = 'book_value_per_share'"
            )
            bvps_after = bvps_after_q[0][0] if bvps_after_q else 0
            if bvps_after > bvps_before:
                pipeline_logger.info(
                    f"Derived book_value_per_share from equity/shares: "
                    f"{bvps_after - bvps_before} new records ({bvps_before} → {bvps_after})"
                )
        except Exception as e:
            pipeline_logger.warning(f"Fundamentals derivation failed: {e}")

    # ── Group B.2: VIX (sequential — yfinance not thread-safe) ──
    # FX + RFR were already started in group_independent_threads above.
    #
    # VIX and Benchmark must run sequentially because yf.download()
    # is NOT thread-safe — concurrent calls cause response mixing
    # (e.g. S&P 500 values stored as VIX data).
    #
    if "vix" in sources and not _check_shutdown("vix"):
        tracker.print_phase_start("vix")
        with metrics.track("vix"):
            with tracker.source_progress("vix", 1) as update:
                dl = _run_vix(
                    db_client,
                    minio_store,
                    pipeline_params,
                    start_date,
                    end_date,
                    run_id,
                    freq,
                    metrics,
                    update,
                    kafka_producer=kafka_producer,
                    mongo_store=mongo_store,
                )
        _append_results(dl)
        tracker.print_phase_complete(
            "vix", metrics._timings.get("vix", 0), metrics._counts["vix"]["total_rows"]
        )

    # ── Group B.3: Benchmark (sequential) ──
    if "benchmark" in sources and not _check_shutdown("benchmark"):
        tracker.print_phase_start("benchmark")
        with metrics.track("benchmark"):
            with tracker.source_progress("benchmark", len(BENCHMARK_SYMBOLS)) as update:
                _run_benchmark(
                    db_client,
                    minio_store,
                    pipeline_params,
                    start_date,
                    end_date,
                    run_id,
                    freq,
                    metrics,
                    update,
                    kafka_producer=kafka_producer,
                    mongo_store=mongo_store,
                )
        tracker.print_phase_complete(
            "benchmark",
            metrics._timings.get("benchmark", 0),
            metrics._counts.get("benchmark", {}).get("total_rows", 0),
        )

    # ── Wait for independent sources (ESG + Sentiment + FX + RFR) ──
    # ESG and Sentiment both call yfinance. Running them concurrently with
    # ratios (8 workers) triggers Yahoo Finance rate limits, causing ~183
    # failures per full run. Join them here before ratios starts.
    # The original join below is kept as a no-op for the already-done threads.
    for t in group_independent_threads:
        t.join(timeout=600)
        if t.is_alive():
            pipeline_logger.warning(
                f"Independent thread {t.name} still alive after 10min — " f"proceeding to ratios anyway"
            )

    # ── Group C: Company ratios (per-ticker parallelised with ThreadPoolExecutor) ──
    #
    # Each worker downloads a *different* symbol — no same-symbol concurrent
    # access.  Runs after ESG + Sentiment have finished so yfinance Ticker.info
    # calls do not compete with other threads for Yahoo's rate limit.
    #
    if "ratios" in sources and not _check_shutdown("ratios"):
        _ratios_workers = pipeline_params.get("ratios_workers", 8)
        tracker.print_parallel_group_start("Group C · company ratios", ["ratios"], _ratios_workers)
        tracker.print_phase_start("ratios")
        with metrics.track("ratios"):
            with tracker.source_progress("ratios", len(ticker_map)) as update:
                _run_ratios(
                    db_client,
                    minio_store,
                    ticker_map,
                    pipeline_params,
                    run_id,
                    freq,
                    metrics,
                    update,
                    kafka_producer=kafka_producer,
                    mongo_store=mongo_store,
                )
        tracker.print_phase_complete(
            "ratios",
            metrics._timings.get("ratios", 0),
            metrics._counts.get("ratios", {}).get("total_rows", 0),
        )

    # ── Group D: Historical ratios (computed from fundamentals + prices) ──
    if "ratios" in sources and not _check_shutdown("historical_ratios"):
        tracker.print_phase_start("historical_ratios")
        with metrics.track("historical_ratios"):
            with tracker.source_progress("historical_ratios", len(ticker_map)) as update:
                _compute_historical_ratios(
                    db_client, ticker_map, run_id, freq, metrics, update,
                )
        tracker.print_phase_complete(
            "historical_ratios",
            metrics._timings.get("historical_ratios", 0),
            metrics._counts.get("historical_ratios", {}).get("total_rows", 0),
        )

    # ── Group E: GDELT historical sentiment backfill ──
    if "sentiment" in sources and not _check_shutdown("sentiment_backfill"):
        tracker.print_phase_start("sentiment_backfill")
        with metrics.track("sentiment_backfill"):
            backfill_count = len(ticker_map)
            with tracker.source_progress("sentiment_backfill", backfill_count) as update:
                _backfill_historical_sentiment(
                    db_client, mongo_store, ticker_map, pipeline_params,
                    start_date, run_id, freq, metrics, update,
                )
        tracker.print_phase_complete(
            "sentiment_backfill",
            metrics._timings.get("sentiment_backfill", 0),
            metrics._counts.get("sentiment_backfill", {}).get("total_rows", 0),
        )

    # ── Independent sources already joined before ratios (above) ──
    # This loop is a safety no-op: threads are already done.

    # ── Flush Kafka events ──
    kafka_producer.flush()

    # ── Stop the shared Live display before printing summary tables ──
    # All source_progress() contexts have exited at this point; stopping the
    # Live cleanly ensures subsequent console.print() calls render normally.
    tracker.close()

    # ── 15. Circuit breaker status ──
    if circuit_breakers:
        tracker.print_circuit_breaker_status(circuit_breakers)

    # ── 16. Downloader statistics ──
    if downloaders:
        tracker.print_downloader_stats(downloaders)

    # ── 17. Pipeline summary ──
    metrics.log_summary()
    tracker.print_summary(metrics.to_dict())

    # ── 18. Post-pipeline data verification ──
    pipeline_logger.info("Running post-pipeline data verification...")
    tracker.print_data_verification(db_client)

    if _shutdown_requested:
        pipeline_logger.warning(
            "Pipeline completed with graceful shutdown " "(some stages may have been skipped)"
        )

    db_client.close()
    mongo_store.close()
    kafka_producer.close()
    pipeline_logger.info("Pipeline completed successfully")


if __name__ == "__main__":
    main()
