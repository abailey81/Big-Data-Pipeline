"""Portfolio construction tests — MinVar (LW + Denoised LW + turnover) + HRP."""

import numpy as np
import pandas as pd
import pytest

from engine.portfolio import (
    PortfolioEngine,
    denoised_ledoit_wolf_cov,
    ledoit_wolf_cov,
)


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
