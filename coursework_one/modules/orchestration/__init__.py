"""
Orchestration sub-package for the Systematic Equity Pipeline.

Splits the pipeline stage functions into focused modules for maintainability,
while Main.py remains the thin entry-point orchestrator.
"""

from modules.orchestration.state import (
    check_shutdown,
    detect_inactive_tickers,
    get_date_range,
    get_db_client,
    inactive_tickers,
    make_log_entry,
    request_shutdown,
    run_health_checks,
    set_inactive_tickers,
)
from modules.orchestration.stage_prices import run_prices
from modules.orchestration.stage_fundamentals import (
    run_fundamentals,
    run_edgar_fundamentals,
    run_finnhub_fundamentals,
    run_nonus_fundamentals_supplement,
)
from modules.orchestration.stage_macro import (
    run_fx,
    run_vix,
    run_risk_free_rate,
    run_benchmark,
    BENCHMARK_SYMBOLS,
)
from modules.orchestration.stage_ratios import (
    run_ratios,
    compute_historical_ratios,
    RATIO_FIELDS,
    FINNHUB_METRIC_FIELDS,
)
from modules.orchestration.stage_esg import run_esg
from modules.orchestration.stage_sentiment import (
    run_news_sentiment,
    backfill_historical_sentiment,
)
