"""News sentiment and historical sentiment backfill stage functions."""

import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from datetime import timedelta

import requests

from modules.db_ops.kafka_ops import TOPICS
from modules.input.gdelt_downloader import GdeltDownloader, parse_gdelt_articles
from modules.input.news_downloader import NewsDownloader, parse_news_articles
from modules.input.newsapi_downloader import NewsApiDownloader, parse_newsapi_articles
from modules.orchestration.state import check_shutdown, inactive_tickers, make_log_entry
from modules.processing.sentiment_scorer import (
    aggregate_sentiment,
    deduplicate_articles,
    score_articles,
)
from modules.utils import pipeline_logger


def backfill_historical_sentiment(
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
        if db_symbol in inactive_tickers():
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
        if check_shutdown("sentiment_backfill"):
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
            if check_shutdown("sentiment_backfill"):
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
                make_log_entry(
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
                make_log_entry(
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


def run_news_sentiment(
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
        if check_shutdown("sentiment"):
            return

        if db_symbol in inactive_tickers():
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
                    make_log_entry(run_id, "sentiment", db_symbol, "SUCCESS", n, frequency=frequency)
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
