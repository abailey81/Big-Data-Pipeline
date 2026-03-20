"""Macro and market data stage functions (FX, VIX, risk-free rate, benchmarks)."""

import threading
from datetime import datetime

import yfinance as yf

from modules.db_ops.kafka_ops import TOPICS
from modules.input.fx_downloader import FxDownloader
from modules.input.risk_free_rate_downloader import RiskFreeRateDownloader
from modules.input.vix_downloader import VixDownloader
from modules.orchestration.state import check_shutdown, make_log_entry
from modules.processing.data_cleaner import (
    clean_fx_dataframe,
    clean_price_dataframe,
    clean_risk_free_rate_dataframe,
    clean_vix_dataframe,
)
from modules.processing.data_quality import DataQualityChecker
from modules.utils import pipeline_logger


def run_fx(
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
        if check_shutdown("fx"):
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
                    make_log_entry(
                        run_id, "fx", pair, "SUCCESS", n, frequency=frequency, start=start_date, end=end_date
                    )
                )
            except Exception as e:
                if metrics:
                    metrics.record_outcome("fx", pair, "FAILED")
                if progress_update:
                    progress_update(pair, "FAILED")
                db_client.insert_log(
                    make_log_entry(run_id, "fx", pair, "FAILED", 0, str(e), frequency, start_date, end_date)
                )

    pipeline_logger.info(f"FX rates: loaded {total_loaded} records total")
    pipeline_logger.info(f"FX downloader stats: {downloader.stats}")
    db_client.update_pipeline_metadata("fx", last_date=end_date)
    return downloader


def run_vix(
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
                    make_log_entry(
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
                    make_log_entry(
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


def run_risk_free_rate(
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
                    make_log_entry(
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
                    make_log_entry(
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


def run_benchmark(
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
    pipeline_logger.info("Starting benchmark index download...")
    total_loaded = 0

    for symbol in BENCHMARK_SYMBOLS:
        if check_shutdown("benchmark"):
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
                        make_log_entry(
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
                make_log_entry(
                    run_id, "benchmark", symbol, "FAILED", 0, str(e), frequency, start_date, end_date
                )
            )
            pipeline_logger.error(f"Benchmark {symbol} failed: {e}")

    pipeline_logger.info(f"Benchmark: loaded {total_loaded} records total")
    db_client.update_pipeline_metadata("benchmark", last_date=end_date)
