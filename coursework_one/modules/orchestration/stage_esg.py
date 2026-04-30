"""ESG sustainability scores stage function."""

import threading
import time as _time

from modules.db_ops.kafka_ops import TOPICS
from modules.input.esg_downloader import EsgDownloader, clean_esg_record
from modules.orchestration.state import check_shutdown, inactive_tickers, make_log_entry
from modules.utils import pipeline_logger


def run_esg(
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
        if check_shutdown("esg"):
            break

        if db_symbol in inactive_tickers():
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
                    make_log_entry(run_id, "esg", db_symbol, "SUCCESS", n, frequency=frequency)
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
