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
from datetime import datetime, timedelta

# Apply a global socket timeout to prevent yfinance HTTP requests from
# hanging indefinitely (yfinance has no built-in request timeout).
# 60 s is long enough for legitimate slow responses but prevents
# indefinite hangs that would block pipeline threads permanently.
socket.setdefaulttimeout(60)

from ift_global import ReadConfig
from ift_global.utils.set_env_var import set_env_variables

from modules.db_ops.kafka_ops import KafkaProducerClient
from modules.db_ops.minio_store import MinioStore
from modules.db_ops.mongo_conn import MongoDBStore
from modules.input.edgar_downloader import is_us_ticker
from modules.input.fx_downloader import FX_PAIRS
from modules.input.get_company_static import get_ticker_list
from modules.orchestration import state as pipeline_state
from modules.orchestration.stage_esg import run_esg
from modules.orchestration.stage_fundamentals import (
    run_edgar_fundamentals,
    run_fundamentals,
)
from modules.orchestration.stage_macro import (
    BENCHMARK_SYMBOLS,
    run_benchmark,
    run_fx,
    run_risk_free_rate,
    run_vix,
)
from modules.orchestration.stage_prices import run_prices
from modules.orchestration.stage_ratios import compute_historical_ratios, run_ratios
from modules.orchestration.stage_sentiment import run_news_sentiment
from modules.orchestration.state import (
    check_shutdown,
    detect_inactive_tickers,
    get_date_range,
    get_db_client,
    request_shutdown,
    run_health_checks,
    set_inactive_tickers,
)
from modules.processing.ticker_utils import prepare_yfinance_ticker
from modules.utils import arg_parse_cmd, generate_run_id, pipeline_logger
from modules.utils.pipeline_metrics import PipelineMetrics
from modules.utils.progress_tracker import PipelineProgressTracker
from modules.utils.scheduler import PipelineScheduler


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
    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

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
    db_client = get_db_client(conf)
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
    start_date, end_date = get_date_range(conf, parsed_args)
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
    health_ok = run_health_checks(
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

    if "prices" in sources and not check_shutdown("prices"):

        def _run_prices_phase():
            tracker.print_phase_start("prices")
            with metrics.track("prices"):
                with tracker.source_progress("prices", len(ticker_map)) as update:
                    dl = run_prices(
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

    if "fundamentals" in sources and not check_shutdown("fundamentals"):

        def _run_fundamentals_phase():
            tracker.print_phase_start("fundamentals")
            with metrics.track("fundamentals"):
                with tracker.source_progress("fundamentals", len(ticker_map)) as update:
                    dls = run_fundamentals(
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

    if "fx" in sources and not check_shutdown("fx"):

        def _run_fx_phase():
            tracker.print_phase_start("fx")
            with metrics.track("fx"):
                with tracker.source_progress("fx", len(FX_PAIRS)) as update:
                    dl = run_fx(
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

    if "risk_free_rate" in sources and not check_shutdown("risk_free_rate"):

        def _run_rfr_phase():
            tracker.print_phase_start("risk_free_rate")
            with metrics.track("risk_free_rate"):
                with tracker.source_progress("risk_free_rate", 1) as update:
                    dl = run_risk_free_rate(
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

    if "esg" in sources and not check_shutdown("esg"):

        def _run_esg_phase():
            tracker.print_phase_start("esg")
            with metrics.track("esg"):
                with tracker.source_progress("esg", len(ticker_map)) as update:
                    esg_dl = run_esg(
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

    if "sentiment" in sources and not check_shutdown("sentiment"):

        def _run_sentiment_phase():
            tracker.print_phase_start("sentiment")
            with metrics.track("sentiment"):
                with tracker.source_progress("sentiment", len(ticker_map)) as update:
                    sentiment_dl = run_news_sentiment(
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
        # EDGAR needs the fundamentals in the DB before starting.
        # FX, RFR, ESG and Sentiment continue running in the background.
        # 2400s = 40min hard cap; each phase has internal per-batch timeouts.
        for t in group_a_threads:
            t.join(timeout=2400)
            if t.is_alive():
                pipeline_logger.warning(
                    f"Phase thread {t.name} still alive after 40min — " f"proceeding to EDGAR anyway"
                )

    # ── Pre-flight delisted detection (after prices phase) ──
    # Uses multi-signal analysis (stale prices + ingestion log) confirmed
    # by live yfinance fast_info checks.  Populates inactive tickers so
    # later phases (fundamentals, EDGAR, ratios, ESG, sentiment) skip them.
    if not check_shutdown("delisted_detection"):
        detected = detect_inactive_tickers(db_client, ticker_map)
        set_inactive_tickers(detected)

    # ── Group A.5: EDGAR fundamentals supplement (US tickers) ──
    #
    # EDGAR provides 5+ years of 10-Q/10-K filings for US companies,
    # filling the gap left by Yahoo Finance (~1.7 years of quarterly data).
    #
    if "fundamentals" in sources and not check_shutdown("edgar_fundamentals"):
        us_count = sum(1 for db, yf, cur in ticker_map if is_us_ticker(db))
        if us_count > 0:
            pipeline_logger.info(
                f"Running EDGAR supplementary fundamentals for {us_count} US tickers..."
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
                    edgar_dl = run_edgar_fundamentals(
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

    # ── Fundamentals retry: re-attempt tickers that returned no data ──
    # yfinance is non-deterministic for non-US tickers — some runs return
    # data, others don't. A retry after a 30s cooldown often succeeds.
    if "fundamentals" in sources and not check_shutdown("fundamentals_retry"):
        try:
            from modules.db_ops.extract_from_query import get_postgres_data as _gpd
            existing_fund = _gpd(
                "SELECT DISTINCT TRIM(symbol) FROM systematic_equity.fundamentals",
                username=db_client._engine.url.username or "postgres",
                password=db_client._engine.url.password or "postgres",
                host=db_client._engine.url.host or "localhost",
                port=str(db_client._engine.url.port or 5438),
                database=db_client._engine.url.database or "fift",
            )
            fund_covered = {r[0] for r in existing_fund} if existing_fund else set()
            active_syms = {t[0] for t in ticker_map if t[0] not in pipeline_state.inactive_tickers()}
            fund_missing = active_syms - fund_covered
            if fund_missing and len(fund_missing) <= 200:
                import time as _time
                pipeline_logger.info(
                    f"Fundamentals retry: {len(fund_missing)} tickers missing — "
                    f"waiting 45s then retrying with slower rate..."
                )
                _time.sleep(45)
                retry_map = [t for t in ticker_map if t[0] in fund_missing]
                # Use slower API delay for retry to avoid rate limits
                retry_params = dict(pipeline_params)
                retry_params["api_delay_seconds"] = 1.0
                retry_params["fundamentals_workers"] = 2
                run_fundamentals(
                    db_client, minio_store, retry_map, retry_params,
                    run_id, freq, metrics, None,
                    kafka_producer=kafka_producer, mongo_store=mongo_store,
                )
                pipeline_logger.info("Fundamentals retry complete")
        except Exception as e:
            pipeline_logger.warning(f"Fundamentals retry failed: {e}")

    # ── Fundamentals post-processing: derive missing fields ──
    # Runs after fundamentals sources complete (yfinance + EDGAR + retry).
    # Fills gaps that exist because yfinance doesn't report certain fields historically.
    if not check_shutdown("fundamentals_derive"):
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
                        "  (symbol, report_date, field_name, field_value, "
                        "period_type, currency, ingestion_timestamp) "
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
                "SELECT COUNT(*) FROM systematic_equity.fundamentals "
                "WHERE field_name = 'book_value_per_share'"
            )
            bvps_before = bvps_before_q[0][0] if bvps_before_q else 0

            with db_client._engine.connect() as conn:
                conn.execute(
                    text(
                        "INSERT INTO systematic_equity.fundamentals "
                        "  (symbol, report_date, field_name, field_value, "
                        "period_type, currency, ingestion_timestamp) "
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
                "SELECT COUNT(*) FROM systematic_equity.fundamentals "
                "WHERE field_name = 'book_value_per_share'"
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
    if "vix" in sources and not check_shutdown("vix"):
        tracker.print_phase_start("vix")
        with metrics.track("vix"):
            with tracker.source_progress("vix", 1) as update:
                dl = run_vix(
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
    if "benchmark" in sources and not check_shutdown("benchmark"):
        tracker.print_phase_start("benchmark")
        with metrics.track("benchmark"):
            with tracker.source_progress("benchmark", len(BENCHMARK_SYMBOLS)) as update:
                run_benchmark(
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

    # ── Cooldown before ratios ──
    # After prices, fundamentals, ESG, and sentiment have all hit yfinance,
    # Yahoo's rate limiter needs time to reset. Without this pause, the first
    # 10-20 ratios tickers get 429/crumb errors before recovering.
    if "ratios" in sources and not check_shutdown("ratios_cooldown"):
        import time as _time
        pipeline_logger.info("Ratios: 45s cooldown to reset yfinance rate limit...")
        _time.sleep(45)

    # ── Group C: Company ratios (per-ticker parallelised with ThreadPoolExecutor) ──
    #
    # Each worker downloads a *different* symbol — no same-symbol concurrent
    # access.  Runs after ESG + Sentiment have finished so yfinance Ticker.info
    # calls do not compete with other threads for Yahoo's rate limit.
    #
    if "ratios" in sources and not check_shutdown("ratios"):
        _ratios_workers = pipeline_params.get("ratios_workers", 8)
        tracker.print_parallel_group_start("Group C · company ratios", ["ratios"], _ratios_workers)
        tracker.print_phase_start("ratios")
        with metrics.track("ratios"):
            with tracker.source_progress("ratios", len(ticker_map)) as update:
                run_ratios(
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

        # ── Ratios retry pass: re-attempt failed tickers after a cooldown ──
        # The main ratios phase may fail some tickers due to transient yfinance
        # errors (crumb expiry, 429 rate limits). This retry pass waits 30s for
        # Yahoo to recover, then re-runs ratios ONLY for tickers that have no
        # snapshot ratios in the DB yet — existing data is preserved via upsert.
        try:
            from modules.db_ops.extract_from_query import get_postgres_data
            existing = get_postgres_data(
                "SELECT DISTINCT TRIM(symbol) FROM systematic_equity.company_ratios "
                "WHERE field_name NOT LIKE '%%_hist'",
                username=db_client._engine.url.username or "postgres",
                password=db_client._engine.url.password or "postgres",
                host=db_client._engine.url.host or "localhost",
                port=str(db_client._engine.url.port or 5438),
                database=db_client._engine.url.database or "fift",
            )
            covered = {r[0] for r in existing} if existing else set()
            active_syms = {t[0] for t in ticker_map if t[0] not in pipeline_state.inactive_tickers()}
            missing = active_syms - covered
            if missing and len(missing) <= 50:
                import time as _time
                pipeline_logger.info(
                    f"Ratios retry: {len(missing)} tickers missing snapshot ratios — "
                    f"waiting 30s then retrying..."
                )
                _time.sleep(30)
                retry_map = [t for t in ticker_map if t[0] in missing]
                run_ratios(
                    db_client, minio_store, retry_map, pipeline_params,
                    run_id, freq, metrics, None,
                    kafka_producer=kafka_producer, mongo_store=mongo_store,
                )
                pipeline_logger.info("Ratios retry complete")
        except Exception as e:
            pipeline_logger.warning(f"Ratios retry pass failed: {e}")

    # ── Group D: Historical ratios (computed from fundamentals + prices) ──
    if "ratios" in sources and not check_shutdown("historical_ratios"):
        tracker.print_phase_start("historical_ratios")
        with metrics.track("historical_ratios"):
            with tracker.source_progress("historical_ratios", len(ticker_map)) as update:
                compute_historical_ratios(
                    db_client, ticker_map, run_id, freq, metrics, update,
                )
        tracker.print_phase_complete(
            "historical_ratios",
            metrics._timings.get("historical_ratios", 0),
            metrics._counts.get("historical_ratios", {}).get("total_rows", 0),
        )

    # ── Group E: GDELT historical sentiment backfill — DISABLED ──
    # Sentiment is kept to recent news only (yfinance + NewsAPI + GDELT gap-fill).
    # Historical backfill via GDELT archive is not needed for the current use case.

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

    if pipeline_state._shutdown_requested:
        pipeline_logger.warning(
            "Pipeline completed with graceful shutdown " "(some stages may have been skipped)"
        )

    db_client.close()
    mongo_store.close()
    kafka_producer.close()
    pipeline_logger.info("Pipeline completed successfully")


if __name__ == "__main__":
    main()
