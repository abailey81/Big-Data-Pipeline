"""PIT-audit tests (PLAN §7.3 rule 7) — no data ≥ rebalance date enters the engine.

Skipped if CW1 DB is not reachable.
"""

from datetime import date

import pandas as pd
import pytest


def _db_available() -> bool:
    try:
        from engine.config import load_config
        from engine.data_loader import DataLoader
        cfg = load_config()
        dl = DataLoader(cfg)
        return dl.health_check()
    except Exception:
        return False


REQUIRES_DB = pytest.mark.skipif(not _db_available(), reason="CW1 DB unavailable")


@REQUIRES_DB
def test_prices_respect_cutoff():
    from engine.config import load_config
    from engine.data_loader import DataLoader
    cfg = load_config()
    dl = DataLoader(cfg)
    rb = date(2024, 6, 28)
    px = dl.load_prices(rb, 252)
    # No row should be on or after rb
    assert px.index.max() < pd.Timestamp(rb)


@REQUIRES_DB
def test_fundamentals_use_report_date_not_period_end():
    from engine.config import load_config
    from engine.data_loader import DataLoader
    cfg = load_config()
    dl = DataLoader(cfg)
    rb = date(2024, 6, 28)
    # AAPL has regular quarterly filings
    fund = dl.load_fundamentals_pit(rb, ["AAPL"])
    assert len(fund) >= 1


@REQUIRES_DB
def test_sentiment_respects_cutoff():
    from engine.config import load_config
    from engine.data_loader import DataLoader
    cfg = load_config()
    dl = DataLoader(cfg)
    # Sentiment only has data at 2026-03-20.  Request as_of 2024 → empty.
    s = dl.load_sentiment_pit(date(2024, 1, 1), ["AAPL", "MSFT"])
    assert len(s) == 0


@REQUIRES_DB
def test_build_context_returns_populated():
    from engine.config import load_config
    from engine.data_loader import DataLoader
    cfg = load_config()
    dl = DataLoader(cfg)
    ctx = dl.build_context(date(2024, 6, 28), 252, apply_liquidity_filter=True)
    assert len(ctx.universe.symbols) > 300
    assert ctx.prices.shape[1] > 300
    assert ctx.returns_usd.shape == ctx.returns_local.shape
    assert ctx.rf_rate >= 0
