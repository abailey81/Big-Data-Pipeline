"""v0.3.2 PIT-lag option (PR-6 / Fix #1).

Regression tests ensuring:
- ``PitLagConfig`` loads with default 0/0 (backwards-compatible).
- ``load_fundamentals_pit`` and ``load_ratios_pit`` accept ``pit_lag_days``
  and forward it to the SQL ``<=`` cutoff.
- ``DataLoader.build_context`` actually *passes* the config values into
  the two loaders (the specific plumbing PR #6 missed).
- Non-zero lag shifts the effective cutoff by the expected number of days.

These tests do NOT require a live Postgres — they mock the SQL read and
assert the cutoff parameter arrives with the right value.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from engine.config import PitLagConfig, load_config
from engine.data_loader import DataLoader


def test_pit_lag_config_default_zero(base_config):
    assert base_config.pit_lag.fundamentals_days == 0
    assert base_config.pit_lag.ratios_days == 0


def test_pit_lag_config_rejects_negative():
    with pytest.raises(Exception):
        PitLagConfig(fundamentals_days=-5)
    with pytest.raises(Exception):
        PitLagConfig(ratios_days=-10)


def test_load_fundamentals_pit_default_cutoff_unchanged(base_config):
    """With default lag = 0, the SQL cutoff equals the as_of date (brief default)."""
    dl = DataLoader(base_config)
    as_of = date(2025, 6, 30)
    captured = {}

    def _fake_read_sql(query, engine, params=None):
        captured.update(params or {})
        return pd.DataFrame()   # empty; loader returns empty DF

    with patch("engine.data_loader.pd.read_sql", side_effect=_fake_read_sql):
        dl.load_fundamentals_pit(as_of, ["AAPL"], pit_lag_days=0)
    assert captured["pit_cutoff"] == as_of


def test_load_fundamentals_pit_positive_lag_shifts_cutoff(base_config):
    """A 45-day lag shifts the SQL cutoff 45 days earlier."""
    dl = DataLoader(base_config)
    as_of = date(2025, 6, 30)
    captured = {}

    def _fake_read_sql(query, engine, params=None):
        captured.update(params or {})
        return pd.DataFrame()

    with patch("engine.data_loader.pd.read_sql", side_effect=_fake_read_sql):
        dl.load_fundamentals_pit(as_of, ["AAPL"], pit_lag_days=45)
    assert captured["pit_cutoff"] == as_of - timedelta(days=45)


def test_load_ratios_pit_positive_lag_shifts_cutoff(base_config):
    dl = DataLoader(base_config)
    as_of = date(2025, 6, 30)
    captured = {}

    def _fake_read_sql(query, engine, params=None):
        captured.update(params or {})
        return pd.DataFrame()

    with patch("engine.data_loader.pd.read_sql", side_effect=_fake_read_sql):
        dl.load_ratios_pit(as_of, ["AAPL"], pit_lag_days=30)
    assert captured["pit_cutoff"] == as_of - timedelta(days=30)


def test_build_context_threads_pit_lag_config():
    """PR-6 blocker: the config value must actually reach the SQL loaders.

    This test constructs a Config with a non-zero PIT lag, builds a
    DataLoader, and confirms that the underlying loaders see the shifted
    cutoff — not just that the function signature accepts the parameter.
    """
    cfg = load_config()
    cfg.pit_lag = PitLagConfig(fundamentals_days=45, ratios_days=20)
    dl = DataLoader(cfg)
    # Short-circuit the SQL-heavy parts
    dl.load_universe = MagicMock(return_value=MagicMock(symbols=["AAPL"], currency_map={}))
    dl._compute_adv = MagicMock(return_value=pd.Series({"AAPL": 1e9}))
    dl.apply_liquidity_filter = MagicMock(
        return_value=(dl.load_universe.return_value, 0)
    )
    dl.load_prices = MagicMock(return_value=pd.DataFrame({"AAPL": [100.0, 101.0]}))
    dl.load_fx = MagicMock(return_value=pd.DataFrame())
    dl._convert_returns_to_usd = MagicMock(return_value=pd.DataFrame())
    dl.load_sentiment_pit = MagicMock(return_value=pd.Series(dtype=float))
    dl.load_vix = MagicMock(return_value=pd.Series(dtype=float))
    dl.load_rf_rate = MagicMock(return_value=0.04)
    dl.load_benchmark = MagicMock(return_value=pd.Series(dtype=float))
    dl.load_fundamentals_pit = MagicMock(return_value=pd.DataFrame())
    dl.load_ratios_pit = MagicMock(return_value=pd.DataFrame())

    as_of = date(2025, 6, 30)
    dl.build_context(as_of, price_lookback_days=60, apply_liquidity_filter=True)

    # THE assertion that kills the dead-code case:
    dl.load_fundamentals_pit.assert_called_once()
    fund_call = dl.load_fundamentals_pit.call_args
    assert fund_call.kwargs.get("pit_lag_days") == 45, (
        f"build_context must forward pit_lag.fundamentals_days=45, "
        f"got {fund_call}. This is exactly the plumbing PR #6 missed."
    )

    dl.load_ratios_pit.assert_called_once()
    rat_call = dl.load_ratios_pit.call_args
    assert rat_call.kwargs.get("pit_lag_days") == 20
