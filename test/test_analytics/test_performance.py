import numpy as np
import pandas as pd

from analytics.performance import (
    PERIODS_PER_YEAR,
    annualized_return,
    annualized_volatility,
    average_exposure,
    max_drawdown,
    risk_off_periods,
    sharpe_ratio,
    sortino_ratio,
    total_return,
)


def test_annualized_return_constant_series() -> None:
    returns = pd.Series([0.01] * PERIODS_PER_YEAR)
    result = annualized_return(returns)
    expected = (1.01**PERIODS_PER_YEAR) - 1.0
    assert np.isclose(result, expected)


def test_annualized_volatility_zero_for_constant_series() -> None:
    returns = pd.Series([0.01] * PERIODS_PER_YEAR)
    result = annualized_volatility(returns)
    assert np.isclose(result, 0.0)


def test_max_drawdown_negative_when_loss_occurs() -> None:
    returns = pd.Series([0.10, -0.20, 0.05])
    result = max_drawdown(returns)
    assert result < 0


def test_sharpe_ratio_nan_when_zero_volatility() -> None:
    returns = pd.Series([0.01] * PERIODS_PER_YEAR)
    result = sharpe_ratio(returns)
    assert np.isnan(result)


def test_sharpe_ratio_uses_raw_return_volatility_with_rf() -> None:
    dates = pd.to_datetime(["2026-01-31", "2026-03-31", "2026-05-31"])
    returns = pd.Series([0.10, 0.00, 0.02], index=dates)
    rf = pd.Series([0.01, 0.01, 0.01], index=dates)

    result = sharpe_ratio(returns, rf=rf, periods_per_year=6)

    excess = returns - rf
    expected = (excess.mean() * 6) / (returns.std(ddof=1) * np.sqrt(6))
    assert np.isclose(result, expected)


def test_sharpe_ratio_uses_date_indexed_rf_alignment() -> None:
    dates = pd.to_datetime(["2026-01-31", "2026-03-31", "2026-05-31"])
    returns = pd.Series([0.03, 0.01, -0.02], index=dates)
    rf = pd.Series([0.02, 0.02, 0.02], index=dates)

    result = sharpe_ratio(returns, rf=rf, periods_per_year=6)

    expected = ((returns - rf).mean() * 6) / (returns.std(ddof=1) * np.sqrt(6))
    no_rf_like_result = (returns.mean() * 6) / (returns.std(ddof=1) * np.sqrt(6))

    assert np.isclose(result, expected)
    assert not np.isclose(result, no_rf_like_result)


def test_total_return_matches_compounded_result() -> None:
    returns = pd.Series([0.10, -0.05])
    result = total_return(returns)
    expected = (1.10 * 0.95) - 1.0
    assert np.isclose(result, expected)


def test_sortino_ratio_nan_when_no_downside_volatility() -> None:
    returns = pd.Series([0.01] * PERIODS_PER_YEAR)
    result = sortino_ratio(returns)
    assert np.isnan(result)


def test_average_exposure_computes_mean() -> None:
    exposure = pd.Series([1.0, 0.8, 0.6, 1.0])
    result = average_exposure(exposure)
    expected = 0.85
    assert np.isclose(result, expected)


def test_risk_off_periods_counts_exposure_below_one() -> None:
    exposure = pd.Series([1.0, 0.8, 0.6, 1.0, 0.4])
    result = risk_off_periods(exposure)
    assert result == 3
