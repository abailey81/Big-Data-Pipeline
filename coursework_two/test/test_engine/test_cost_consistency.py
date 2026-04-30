"""v0.3.2 cost-consistency fix (PR-6 / Fix #2).

Regression test for the bug where ``BacktestEngine._recent_turnover`` was
comparing the current weights to an *empty* ``Series`` — which makes
``one_way_turnover`` equal to ``0.5 · Σ|w| ≈ 1.0`` for any dollar-neutral
book and inflates the cost drag applied to every net-return column in
``portfolio_returns.parquet``.

The fix added a ``_prev_weights_for_cost`` cache snapshotted BEFORE the
main-loop overwrites ``_prev_weights[strategy]`` with the new ``w_scaled``.
``_recent_turnover`` now compares the new weights against that cached
previous snapshot, matching the main-loop's correct calculation and
reconciling the exposure-log ``turnover_1way`` column with the cost drag
implied by the ``portfolio_returns`` gross-net gap.
"""

from __future__ import annotations

import pandas as pd
import pytest

from engine.backtest import BacktestEngine
from engine.costs import CostModel
from engine.data_loader import DataLoader
from engine.factors import FactorEngine
from engine.portfolio import PortfolioEngine
from engine.types import Strategy
from engine.zscore import ZScoreEngine


def _make_engine(base_config):
    dl = DataLoader(base_config)
    return BacktestEngine(
        cfg=base_config,
        data_loader=dl,
        factor_engine=FactorEngine(base_config),
        zscore_engine=ZScoreEngine(base_config),
        portfolio_engine=PortfolioEngine(base_config),
        cost_model=CostModel(base_config),
    )


def test_recent_turnover_uses_cached_previous_weights(base_config):
    """Two consecutive rebalances → turnover reflects the actual weight change.

    Before the fix the function returned 0.5 · Σ|w_new| (≈ 1.0) regardless
    of whether anything actually changed.  After the fix, identical weights
    on two consecutive calls should produce turnover ≈ 0.
    """
    eng = _make_engine(base_config)
    strategy = Strategy.DYNAMIC_GRID

    w = pd.Series({"AAPL": 0.04, "MSFT": 0.03, "GOOG": -0.05, "AMZN": -0.02})

    # Simulate the main-loop ordering: cache pre-update weights, then update.
    eng._prev_weights_for_cost[strategy] = eng._prev_weights.get(
        strategy, pd.Series(dtype=float)
    ).copy()
    eng._prev_weights[strategy] = w.copy()
    t_first = eng._recent_turnover(strategy)   # first rebalance: prev was empty → full |w|

    # Second rebalance with identical weights → turnover should be ~0
    eng._prev_weights_for_cost[strategy] = eng._prev_weights.get(
        strategy, pd.Series(dtype=float)
    ).copy()
    eng._prev_weights[strategy] = w.copy()
    t_second = eng._recent_turnover(strategy)

    # First rebalance against empty prev: expected 0.5 * sum(|w|)
    expected_first = 0.5 * w.abs().sum()
    assert abs(t_first - expected_first) < 1e-9

    # Second rebalance: identical weights → zero turnover
    assert t_second < 1e-9, (
        f"Expected ~0 turnover when weights are unchanged, got {t_second:.4f}. "
        "This is the bug PR-6 Fix #2 was designed to catch."
    )


def test_recent_turnover_matches_main_loop_calculation(base_config):
    """_recent_turnover must equal the main-loop's one_way_turnover computation."""
    eng = _make_engine(base_config)
    strategy = Strategy.DYNAMIC_GRID

    w_prev = pd.Series({"AAPL": 0.05, "MSFT": 0.03})
    w_new = pd.Series({"AAPL": 0.03, "MSFT": 0.05, "GOOG": -0.04, "AMZN": -0.04})

    # Simulate main-loop ordering
    eng._prev_weights[strategy] = w_prev.copy()                              # pre-rebalance state
    eng._prev_weights_for_cost[strategy] = eng._prev_weights[strategy].copy()  # snapshot
    eng._prev_weights[strategy] = w_new.copy()                               # post-rebalance update

    t_recent = eng._recent_turnover(strategy)
    t_main_loop = eng.cost_model.one_way_turnover(w_new, w_prev)
    assert abs(t_recent - t_main_loop) < 1e-9, (
        "_recent_turnover must agree with the main-loop cost calculation — "
        "the only valid definition of turnover for a rebalance."
    )


def test_cache_field_exists_on_engine(base_config):
    """Schema regression — the cache field must be present on BacktestEngine."""
    eng = _make_engine(base_config)
    assert hasattr(eng, "_prev_weights_for_cost")
    assert isinstance(eng._prev_weights_for_cost, dict)
