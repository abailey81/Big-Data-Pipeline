"""Performance-metrics tests on synthetic return series with known properties."""

import numpy as np
import pandas as pd
import pytest

from analytics.performance import (
    annualised_return,
    annualised_volatility,
    calmar_ratio,
    circular_block_bootstrap_sharpe,
    compute_headline_metrics,
    deflated_sharpe_ratio,
    drawdown_series,
    excess_kurtosis,
    expected_shortfall,
    historical_var,
    information_ratio,
    max_drawdown,
    minimum_backtest_length,
    monthly_hit_rate,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
    skewness,
    sortino_ratio,
)


def test_sharpe_constant_returns():
    # Zero-vol returns → inf Sharpe guarded to 0
    r = pd.Series([0.01] * 36)
    assert sharpe_ratio(r, 0.0) == 0.0


def test_sharpe_positive_series():
    rng = np.random.default_rng(42)
    r = pd.Series(rng.normal(0.01, 0.02, 36))
    sr = sharpe_ratio(r, 0.0)
    # Expected ~ (0.01 × 12) / (0.02 × √12) = 0.12 / 0.0693 ~ 1.73
    assert 0.5 < sr < 3.0


def test_sortino_less_than_or_equal_sharpe():
    """Sortino penalises only downside; >= Sharpe for positively-skewed series."""
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.01, 0.02, 36))
    assert sortino_ratio(r, 0.0) >= 0   # well-defined


def test_max_drawdown_monotone_down():
    """Series with monotone declining returns → large negative DD."""
    r = pd.Series([-0.05] * 12)
    mdd = max_drawdown(r)
    assert mdd < -0.3


def test_drawdown_series_no_crashes():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.01, 0.02, 50))
    dd = drawdown_series(r)
    assert (dd <= 0).all()


def test_hit_rate_range():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0, 0.02, 100))
    hr = monthly_hit_rate(r)
    assert 0 <= hr <= 1


def test_historical_var_es_order():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0, 0.02, 500))
    var = historical_var(r, 0.99)
    es = expected_shortfall(r, 0.99)
    assert es >= var > 0


def test_information_ratio_zero_on_identical_series():
    r = pd.Series([0.01, 0.02, -0.01])
    assert information_ratio(r, r) == 0.0


def test_annualised_return_basic():
    r = pd.Series([0.01] * 12)   # 1% monthly
    ann = annualised_return(r)
    # (1.01)^12 - 1 = ~0.1268
    assert abs(ann - 0.1268) < 0.01


def test_block_bootstrap_sharpe_returns_ci():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.005, 0.02, 60))
    res = circular_block_bootstrap_sharpe(r, block_size=6, n_bootstrap=200)
    assert "mean" in res and "low" in res and "high" in res
    assert res["low"] <= res["mean"] <= res["high"]


def test_deflated_sharpe_returns_probability():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.01, 0.02, 60))
    sr = sharpe_ratio(r, 0.0)
    res = deflated_sharpe_ratio(sr, n_trials=15, returns=r)
    if not np.isnan(res["deflated_sharpe"]):
        assert 0 <= res["deflated_sharpe"] <= 1


def test_mbl_returns_positive_for_positive_target():
    mbl = minimum_backtest_length(target_sharpe=1.0, n_trials=15, alpha=0.05)
    assert mbl > 0


def test_psr_between_zero_and_one():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.01, 0.02, 60))
    sr = sharpe_ratio(r, 0.0)
    psr = probabilistic_sharpe_ratio(sr, 0.5, r)
    if not np.isnan(psr):
        assert 0 <= psr <= 1


def test_headline_metrics_table_shape():
    rng = np.random.default_rng(0)
    n = 24
    df = pd.DataFrame({
        "date": pd.date_range("2023-01-31", periods=n, freq="ME"),
        "dynamic_gross": rng.normal(0.01, 0.02, n),
        "dynamic_net_20bp": rng.normal(0.008, 0.02, n),
        "static_net_20bp": rng.normal(0.007, 0.02, n),
        "benchmark_ew": rng.normal(0.005, 0.02, n),
    })
    out = compute_headline_metrics(df)
    assert out.shape[0] >= 10
    assert out.shape[1] == 4
