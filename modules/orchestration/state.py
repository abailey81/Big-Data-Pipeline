"""
Shared pipeline state and utility functions.

Provides the mutable global state (shutdown flag, inactive tickers) and
stateless utility functions used by all orchestration stage modules.
"""

import signal
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from modules.db_ops.postgres_config import PostgresConfig
from modules.db_ops.sql_conn import DatabaseMethods
from modules.utils import pipeline_logger
from modules.utils.health_check import PipelineHealthChecker

# ── Global shutdown flag for graceful termination ──
_shutdown_requested = False
_shutdown_lock = threading.Lock()

# ── Inactive/delisted tickers detected via database query ──
_inactive_tickers: set[str] = set()
_inactive_lock = threading.Lock()


def request_shutdown(signum=None, frame=None):
    """Handle SIGINT/SIGTERM for graceful pipeline shutdown.

    Sets a global flag that is checked between processing stages.
    Currently running downloads complete, but no new stages start.

    :param signum: Signal number (2=SIGINT, 15=SIGTERM)
    :param frame: Current stack frame (unused)
    """
    global _shutdown_requested
    if signum is not None:
        sig_name = signal.Signals(signum).name
        pipeline_logger.warning(
            f"Received {sig_name} — initiating graceful shutdown. "
            f"Current stage will complete before exit."
        )
    _shutdown_requested = True


def check_shutdown(stage: str = "") -> bool:
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


def inactive_tickers() -> set[str]:
    """Return the current set of confirmed inactive/delisted tickers."""
    return _inactive_tickers


def set_inactive_tickers(tickers: set[str]) -> None:
    """Update the global inactive tickers set."""
    global _inactive_tickers
    with _inactive_lock:
        _inactive_tickers = tickers


def make_log_entry(
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


def get_date_range(conf: dict, parsed_args) -> tuple[str, str]:
    """Calculate start and end dates for data download.

    Uses either explicit CLI dates or derives from frequency/lookback_years.

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


def get_db_client(conf: dict) -> DatabaseMethods:
    """Create a DatabaseMethods client from configuration.

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


def run_health_checks(db_client, minio_store, conf, tracker, mongo_store=None, kafka_producer=None):
    """Execute pre-flight health checks and display results.

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
    tracker.print_health_checks(results)

    if not checker.critical_healthy(results):
        pipeline_logger.error("Critical health checks failed — aborting pipeline")
        for r in results:
            if not r.healthy:
                pipeline_logger.error(f"  FAIL: {r.name} — {r.message}")
        return False

    for r in results:
        if not r.healthy:
            pipeline_logger.warning(
                f"Non-critical health check failed: {r.name} — "
                f"{r.message}. Pipeline will continue with degraded mode."
            )

    return True


def detect_inactive_tickers(db_client, ticker_map=None) -> set[str]:
    """Detect inactive/delisted tickers using multi-signal analysis + live verification.

    Three independent signals are combined:

    1. **Stale price signal** — tickers whose most recent price is older than 180 days.
    2. **Ingestion-log signal** — tickers that FAILED in prices in the most recent run.
    3. **Live verification** — each candidate is checked via yf.Ticker().fast_info.

    :param db_client: PostgreSQL database client with read_query method
    :param ticker_map: Optional list of (db_symbol, yf_ticker, currency) tuples.
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
                f"Signal 2 (ingestion log): {len(log_symbols)} tickers "
                f"FAILED in prices across recent runs"
            )
        candidates |= log_symbols
    except Exception as exc:
        pipeline_logger.warning(f"Ingestion-log signal query failed: {exc}")

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
                confirmed_inactive.add(futures[fut])

    if confirmed_inactive:
        pipeline_logger.info(
            f"Pre-flight delisted detection complete: {len(confirmed_inactive)}/{len(candidates)} "
            f"confirmed inactive (will skip in fundamentals, ratios, ESG, sentiment)"
        )
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
