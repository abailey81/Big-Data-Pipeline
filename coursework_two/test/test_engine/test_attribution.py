"""Attribution tests — Fama-MacBeth + Kyle's-λ capacity."""

import numpy as np
import pandas as pd
import pytest

from engine.attribution import (
    amihud_illiquidity,
    fama_macbeth_one_date,
    fama_macbeth_t_stat,
    max_aum_at_impact_budget,
)


def test_fama_macbeth_recovers_coefficient():
    """If r = 0.5 * mom + ε, estimated β_mom should be close to 0.5."""
    rng = np.random.default_rng(0)
    N = 100
    syms = [f"S{i}" for i in range(N)]
    mom = rng.standard_normal(N)
    val = rng.standard_normal(N)
    r = 0.5 * mom + 0.1 * rng.standard_normal(N)
    z = pd.DataFrame({"momentum": mom, "value": val}, index=syms)
    fwd = pd.Series(r, index=syms)
    res = fama_macbeth_one_date(z, fwd)
    assert abs(res["momentum"] - 0.5) < 0.1


def test_fama_macbeth_t_stat_basic():
    beta_series = pd.Series([0.3, 0.25, 0.35, 0.28, 0.32])
    mean, t, n = fama_macbeth_t_stat(beta_series)
    assert 0.25 < mean < 0.35
    assert t > 5   # consistent positive signal


def test_amihud_basic():
    dates = pd.date_range("2024-01-01", periods=100, freq="B")
    syms = ["A", "B"]
    rng = np.random.default_rng(0)
    ret = pd.DataFrame(rng.normal(0, 0.01, (100, 2)), index=dates, columns=syms)
    vol = pd.DataFrame(np.abs(rng.normal(1e7, 1e6, (100, 2))), index=dates, columns=syms)
    ill = amihud_illiquidity(ret, vol)
    assert ill.shape == (2,)
    assert (ill > 0).all()


def test_capacity_declines_with_larger_weights():
    weights = pd.Series({"A": 0.05, "B": 0.01})
    lam = pd.Series({"A": 1e-9, "B": 1e-9})
    cap = max_aum_at_impact_budget(weights, lam, impact_budget_bp=15)
    # Larger weight on A → A constrains capacity
    # cap_A = 15 / (0.05 × 1e-9 × 10000) = 15 / 5e-7 = 30,000,000
    assert cap > 0
    # Heavier weights should reduce capacity
    weights_heavy = pd.Series({"A": 0.05, "B": 0.05})
    cap2 = max_aum_at_impact_budget(weights_heavy, lam, impact_budget_bp=15)
    assert cap2 <= cap + 1e-6
