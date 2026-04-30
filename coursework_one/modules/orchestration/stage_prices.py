"""Stage module for daily price data download, cleaning, and loading.

Extracts the prices pipeline phase from Main.py into a focused,
independently testable module.  All original logic, comments, and
docstrings are preserved verbatim.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from datetime import datetime

from modules.db_ops.kafka_ops import TOPICS
from modules.input.price_downloader import PriceDownloader
from modules.orchestration.state import check_shutdown, make_log_entry
from modules.processing.data_cleaner import clean_price_dataframe
from modules.processing.data_quality import DataQualityChecker
from modules.utils import pipeline_logger


def run_prices(
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
                        make_log_entry(
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
                        make_log_entry(
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
                make_log_entry(
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
        if check_shutdown("prices batch"):
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
