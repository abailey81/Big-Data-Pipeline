from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd


# v6 is bi-monthly, so the default annualisation factor should be 6.
PERIODS_PER_YEAR = 6


def _to_clean_series(series: pd.Series) -> pd.Series:
    """Convert a Series to float and drop missing values."""
    return series.dropna().astype(float)


def total_return(returns: pd.Series) -> float:
    """Compute total compounded return over the full sample."""
    returns = _to_clean_series(returns)
    if returns.empty:
        return np.nan

    cumulative = (1.0 + returns).prod()
    return float(cumulative - 1.0)


def annualized_return(
    returns: pd.Series,
    periods_per_year: int = PERIODS_PER_YEAR,
) -> float:
    """Compute annualised compounded return."""
    returns = _to_clean_series(returns)
    if returns.empty:
        return np.nan

    cumulative = (1.0 + returns).prod()
    n_periods = len(returns)

    # Guard against pathological cases where cumulative NAV is non-positive.
    if cumulative <= 0.0:
        return np.nan

    return float(cumulative ** (periods_per_year / n_periods) - 1.0)


def annualized_volatility(
    returns: pd.Series,
    periods_per_year: int = PERIODS_PER_YEAR,
) -> float:
    """Compute annualised sample volatility."""
    returns = _to_clean_series(returns)
    if len(returns) < 2:
        return np.nan

    return float(returns.std(ddof=1) * np.sqrt(periods_per_year))


def downside_volatility(
    returns: pd.Series,
    periods_per_year: int = PERIODS_PER_YEAR,
) -> float:
    """
    Compute annualised downside volatility using downside deviation
    relative to 0 per-period return.
    """
    returns = _to_clean_series(returns)
    if returns.empty:
        return np.nan

    downside = np.minimum(returns, 0.0)
    downside_dev = np.sqrt(np.mean(np.square(downside)))

    if np.isclose(downside_dev, 0.0):
        return np.nan

    return float(downside_dev * np.sqrt(periods_per_year))


def sharpe_ratio(
    returns: pd.Series,
    rf: Optional[pd.Series] = None,
    periods_per_year: int = PERIODS_PER_YEAR,
) -> float:
    """Compute annualised Sharpe ratio using v6.5 reporting convention."""
    returns = _to_clean_series(returns)
    if returns.empty:
        return np.nan

    if rf is None:
        excess = returns
    else:
        rf = rf.reindex(returns.index).fillna(0.0).astype(float)
        excess = returns - rf

    if len(excess) < 2:
        return np.nan

    # Match v6.5 headline reporting: annualised excess mean over raw-return vol.
    ann_vol = annualized_volatility(returns, periods_per_year)
    if pd.isna(ann_vol) or np.isclose(ann_vol, 0.0):
        return np.nan

    ann_excess_mean = float(excess.mean() * periods_per_year)
    return ann_excess_mean / ann_vol


def sortino_ratio(
    returns: pd.Series,
    rf: Optional[pd.Series] = None,
    periods_per_year: int = PERIODS_PER_YEAR,
) -> float:
    """Compute annualised Sortino ratio."""
    returns = _to_clean_series(returns)
    if returns.empty:
        return np.nan

    if rf is None:
        excess = returns
    else:
        rf = rf.reindex(returns.index).fillna(0.0).astype(float)
        excess = returns - rf

    dvol = downside_volatility(excess, periods_per_year)
    if pd.isna(dvol) or np.isclose(dvol, 0.0):
        return np.nan

    ann_excess_ret = annualized_return(excess, periods_per_year)
    if pd.isna(ann_excess_ret):
        return np.nan

    return float(ann_excess_ret / dvol)


def cumulative_nav(returns: pd.Series, initial_nav: float = 1.0) -> pd.Series:
    """Convert period returns into a cumulative NAV series."""
    returns = _to_clean_series(returns)
    if returns.empty:
        return pd.Series(dtype=float)

    nav = initial_nav * (1.0 + returns).cumprod()
    nav.name = "nav"
    return nav


def drawdown_series(returns: pd.Series) -> pd.Series:
    """Compute running drawdown series from returns."""
    nav = cumulative_nav(returns)
    if nav.empty:
        return pd.Series(dtype=float)

    running_max = nav.cummax()
    dd = nav / running_max - 1.0
    dd.name = "drawdown"
    return dd


def max_drawdown(returns: pd.Series) -> float:
    """Compute maximum drawdown."""
    dd = drawdown_series(returns)
    if dd.empty:
        return np.nan

    return float(dd.min())


def hit_rate(returns: pd.Series) -> float:
    """Fraction of periods with positive return."""
    returns = _to_clean_series(returns)
    if returns.empty:
        return np.nan

    return float((returns > 0).mean())


def average_exposure(exposure: pd.Series) -> float:
    """Mean portfolio exposure across periods."""
    exposure = _to_clean_series(exposure)
    if exposure.empty:
        return np.nan

    return float(exposure.mean())


def risk_off_periods(
    exposure: pd.Series,
    full_exposure: float = 1.0,
    tol: float = 1e-9,
) -> int:
    """Count periods where portfolio exposure is below full exposure."""
    exposure = _to_clean_series(exposure)
    if exposure.empty:
        return 0

    return int((exposure < (full_exposure - tol)).sum())


def risk_off_ratio(
    exposure: pd.Series,
    full_exposure: float = 1.0,
    tol: float = 1e-9,
) -> float:
    """Fraction of periods spent in risk-off mode."""
    exposure = _to_clean_series(exposure)
    if exposure.empty:
        return np.nan

    return float((exposure < (full_exposure - tol)).mean())


def _default_strategy_cols(
    returns_df: pd.DataFrame,
    exposure_col: str,
) -> list[str]:
    """
    Infer which columns should be treated as return series.
    Prefer v6-style names first; otherwise fall back to numeric columns
    except exposure.
    """
    preferred = [
        "gross_return",
        "net_return",
        "benchmark_return",
        "benchmark",
        "strategy_return",
    ]
    found = [col for col in preferred if col in returns_df.columns]
    if found:
        return found

    numeric_cols = returns_df.select_dtypes(include=[np.number]).columns.tolist()
    return [col for col in numeric_cols if col != exposure_col]


def compute_headline_metrics(
    returns_df: pd.DataFrame,
    rf_series: Optional[pd.Series] = None,
    periods_per_year: int = PERIODS_PER_YEAR,
    strategy_cols: Optional[Sequence[str]] = None,
    exposure_col: str = "exposure",
    full_exposure: float = 1.0,
) -> pd.DataFrame:
    """
    Compute report-ready headline metrics for each return series in returns_df.

    Expected v6 input shape:
    - return columns such as gross_return / net_return / benchmark_return
    - optional exposure column such as exposure
    """
    if strategy_cols is None:
        strategy_cols = _default_strategy_cols(returns_df, exposure_col)

    rows: list[dict[str, float | int | str]] = []

    for col in strategy_cols:
        if col not in returns_df.columns:
            continue

        series = _to_clean_series(returns_df[col])
        if series.empty:
            continue

        row: dict[str, float | int | str] = {
            "strategy": col,
            "n_periods": int(len(series)),
            "periods_per_year": int(periods_per_year),
            "total_return": total_return(series),
            "annualized_return": annualized_return(series, periods_per_year),
            "annualized_volatility": annualized_volatility(series, periods_per_year),
            "sharpe_ratio": sharpe_ratio(series, rf_series, periods_per_year),
            "sortino_ratio": sortino_ratio(series, rf_series, periods_per_year),
            "max_drawdown": max_drawdown(series),
            "hit_rate": hit_rate(series),
        }

        # Exposure metrics are meaningful for the strategy return series,
        # but normally not for benchmark rows.
        is_benchmark = "benchmark" in col.lower()

        if exposure_col in returns_df.columns and not is_benchmark:
            exposure = returns_df.loc[series.index, exposure_col]
            row["average_exposure"] = average_exposure(exposure)
            row["risk_off_periods"] = risk_off_periods(
                exposure,
                full_exposure=full_exposure,
            )
            row["risk_off_ratio"] = risk_off_ratio(
                exposure,
                full_exposure=full_exposure,
            )
        else:
            row["average_exposure"] = np.nan
            row["risk_off_periods"] = np.nan
            row["risk_off_ratio"] = np.nan

        rows.append(row)

    return pd.DataFrame(rows)
