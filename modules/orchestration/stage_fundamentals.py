"""
Fundamentals stage functions for the Systematic Equity Pipeline.

Handles Yahoo Finance, SEC EDGAR, Finnhub, and non-US supplement
(FMP, SimFin, Alpha Vantage) fundamentals data ingestion.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from datetime import datetime

import pandas as pd

from modules.db_ops.kafka_ops import TOPICS
from modules.input.alphavantage_downloader import AlphaVantageFundamentalsDownloader
from modules.input.edgar_downloader import (
    EdgarFundamentalsDownloader,
    extract_edgar_fundamentals,
    is_us_ticker,
)
from modules.input.finnhub_downloader import (
    FinnhubFundamentalsDownloader,
    extract_finnhub_fundamentals,
    is_non_us_ticker,
)
from modules.input.fmp_downloader import FmpFundamentalsDownloader
from modules.input.fundamentals_downloader import FundamentalsDownloader
from modules.input.simfin_downloader import SimFinFundamentalsDownloader
from modules.orchestration.state import check_shutdown, inactive_tickers, make_log_entry
from modules.processing.data_cleaner import clean_fundamentals_data
from modules.processing.data_quality import DataQualityChecker
from modules.utils import pipeline_logger
from modules.utils.concurrent_executor import ConcurrentDownloadExecutor


def run_fundamentals(
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

        if check_shutdown("fundamentals"):
            return

        if db_symbol in inactive_tickers():
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
                        make_log_entry(run_id, "fundamentals", db_symbol, "SUCCESS", n, frequency=frequency)
                    )
                except Exception as e:
                    if metrics:
                        metrics.record_outcome("fundamentals", db_symbol, "FAILED")
                    if progress_update:
                        progress_update(db_symbol, "FAILED")
                    db_client.insert_log(
                        make_log_entry(run_id, "fundamentals", db_symbol, "FAILED", 0, str(e), frequency)
                    )
        else:
            if metrics:
                metrics.record_outcome("fundamentals", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            db_client.insert_log(
                make_log_entry(run_id, "fundamentals", db_symbol, "SKIPPED", 0, frequency=frequency)
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


def run_edgar_fundamentals(
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
        if check_shutdown("edgar_fundamentals"):
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
                        make_log_entry(
                            run_id, "edgar_fundamentals", db_symbol, "SUCCESS", n, frequency=frequency
                        )
                    )
                except Exception as e:
                    if metrics:
                        metrics.record_outcome("edgar_fundamentals", db_symbol, "FAILED")
                    if progress_update:
                        progress_update(db_symbol, "FAILED")
                    db_client.insert_log(
                        make_log_entry(
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


def run_finnhub_fundamentals(
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
        if check_shutdown("finnhub_fundamentals"):
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
                        make_log_entry(
                            run_id, "finnhub_fundamentals", db_symbol, "SUCCESS", n, frequency=frequency
                        )
                    )
                except Exception as e:
                    if metrics:
                        metrics.record_outcome("finnhub_fundamentals", db_symbol, "FAILED")
                    if progress_update:
                        progress_update(db_symbol, "FAILED")
                    db_client.insert_log(
                        make_log_entry(
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


def run_nonus_fundamentals_supplement(
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
    non_us = [(db, yf, cur) for db, yf, cur in ticker_map if is_non_us_ticker(db)]
    if not non_us:
        pipeline_logger.info("Non-US supplement: no non-US tickers to process")
        return None

    fmp_key = os.environ.get("FMP_API_KEY", "")
    simfin_key = os.environ.get("SIMFIN_API_KEY", "")
    av_keys = [os.environ.get(f"ALPHA_VANTAGE_KEY_{i}", "") for i in range(1, 20)]
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

    need_supplement = [t for t in non_us if t[0] not in well_covered and t[0] not in inactive_tickers()]

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
        if check_shutdown("nonus_supplement"):
            return
        if db_symbol in inactive_tickers():
            if metrics:
                metrics.record_outcome("nonus_supplement", db_symbol, "SKIPPED")
            if progress_update:
                progress_update(db_symbol, "SKIPPED")
            db_client.insert_log(
                make_log_entry(run_id, "nonus_supplement", db_symbol, "SKIPPED", 0, "inactive", frequency)
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
                    make_log_entry(
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
                    make_log_entry(
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
                make_log_entry(
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
