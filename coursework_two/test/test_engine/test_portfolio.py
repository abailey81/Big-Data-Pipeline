"""Portfolio construction tests — MinVar (LW + Denoised LW + turnover) + HRP."""

import numpy as np
import pandas as pd
import pytest

from engine.portfolio import (
    PortfolioEngine,
    _iterative_cap,
    denoised_ledoit_wolf_cov,
    ledoit_wolf_cov,
)


def test_iterative_cap_preserves_mass_when_feasible():
    """50 names with a 5% cap has enough head-room: total mass preserved at 1."""
    np.random.seed(0)
    scores = np.abs(np.random.randn(50))
    w = pd.Series(scores / scores.sum())
    w2 = _iterative_cap(w, 0.05)
    assert w2.max() <= 0.05 + 1e-9
    assert abs(w2.sum() - 1.0) < 1e-8
    assert (w2 >= -1e-12).all()


def test_iterative_cap_sparse_universe_holds_cash():
    """4 names with a 5% cap cannot use all the mass — residual cash is 0.8."""
    w = pd.Series([0.40, 0.30, 0.20, 0.10])
    w2 = _iterative_cap(w, 0.05)
    assert w2.max() <= 0.05 + 1e-9
    # Every name pinned at cap, so sum = 4 * 0.05 = 0.20
    assert abs(w2.sum() - 0.20) < 1e-9


def test_iterative_cap_no_op_when_all_below_cap():
    """If every weight is already below the cap, the input is returned unchanged."""
    w = pd.Series(np.ones(20) / 20)  # every name at 5%, cap 10% → no-op
    w2 = _iterative_cap(w, 0.10)
    assert np.allclose(w2.values, w.values)


def test_iterative_cap_redistributes_single_spike():
    """One over-cap name, the excess goes to the remaining uncapped names."""
    w = pd.Series([0.20, 0.02, 0.02, 0.02, 0.02, 0.02])  # sums 0.30
    w2 = _iterative_cap(w, 0.05)
    assert w2.max() <= 0.05 + 1e-9
    # Mass preserved (0.30 in, 0.30 out) because the cap-available headroom is enough
    assert abs(w2.sum() - w.sum()) < 1e-9


def test_lw_cov_psd(synthetic_returns):
    Sigma = ledoit_wolf_cov(synthetic_returns)
    eig = np.linalg.eigvalsh(Sigma)
    assert (eig > -1e-10).all()


def test_denoised_lw_cov_psd(synthetic_returns):
    Sigma = denoised_ledoit_wolf_cov(synthetic_returns)
    eig = np.linalg.eigvalsh(Sigma)
    assert (eig > -1e-10).all()


def test_minvar_lw_feasible_weights(base_config, synthetic_returns):
    base_config.portfolio.construction = "minvar_lw"
    pe = PortfolioEngine(base_config)
    w = pe.optimise_leg(synthetic_returns, list(synthetic_returns.columns))
    assert abs(w.sum() - 1.0) < 1e-4
    assert (w >= -1e-8).all()
    assert w.max() <= base_config.portfolio.max_weight_per_stock + 1e-6


def test_minvar_denoised_lw_feasible(base_config, synthetic_returns):
    base_config.portfolio.construction = "minvar_denoised_lw"
    pe = PortfolioEngine(base_config)
    w = pe.optimise_leg(synthetic_returns, list(synthetic_returns.columns))
    assert abs(w.sum() - 1.0) < 1e-4
    assert (w >= -1e-8).all()


def test_minvar_turnover_reduces_change(base_config, synthetic_returns):
    """With high turnover penalty, weights should stay closer to previous."""
    base_config.portfolio.construction = "minvar_turnover"
    base_config.portfolio.turnover_penalty_lambda = 100.0   # very strong
    pe = PortfolioEngine(base_config)
    prev = pd.Series(1.0 / len(synthetic_returns.columns), index=synthetic_returns.columns)
    w = pe.optimise_leg(synthetic_returns, list(synthetic_returns.columns), prev)
    # Should be close to previous equal-weight
    diff = (w - prev).abs().sum()
    assert diff < 0.3   # much less than unbounded change


def test_hrp_feasible(base_config, synthetic_returns):
    base_config.portfolio.construction = "hrp"
    pe = PortfolioEngine(base_config)
    w = pe.optimise_leg(synthetic_returns, list(synthetic_returns.columns))
    assert abs(w.sum() - 1.0) < 1e-4
    assert (w >= -1e-8).all()
