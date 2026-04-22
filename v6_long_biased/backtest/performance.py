"""
backtest/performance.py -- Performance analytics with FREQUENCY-AWARE annualization.

FIX (Codex audit #1): Auto-detects periods_per_year from the return series.
  Monthly -> sqrt(12), bi-monthly -> sqrt(6), etc.
  All annualization uses detected frequency.
"""

import pandas as pd
import numpy as np


def _detect_periods_per_year(dates: pd.DatetimeIndex) -> float:
    """Auto-detect rebalancing frequency from the return dates.

    Returns periods_per_year: 12 for monthly, 6 for bi-monthly, 4 for quarterly.
    """
    if len(dates) < 2:
        return 12.0  # fallback
    # Median gap between consecutive dates in days
    gaps = pd.Series(dates).diff().dt.days.dropna()
    median_gap = gaps.median()
    # Convert to periods per year
    periods = 365.25 / max(median_gap, 1)
    # Round to nearest standard frequency
    if periods > 10:
        return 12.0   # monthly
    elif periods > 5:
        return 6.0    # bi-monthly
    elif periods > 3:
        return 4.0    # quarterly
    elif periods > 1.5:
        return 2.0    # semi-annual
    else:
        return 1.0    # annual


def compute_performance_metrics(
    period_returns: pd.Series,
    rf_series: pd.Series,
    benchmark_returns: pd.Series = None,
    turnovers: list = None,
) -> dict:
    """Compute full suite of performance metrics.

    FREQUENCY-AWARE: auto-detects periods_per_year from the dates in
    period_returns, so sqrt(N) and annualization are correct for any
    rebalancing frequency (monthly, bi-monthly, quarterly, etc.).
    """
    if len(period_returns) == 0:
        return {}

    ppy = _detect_periods_per_year(period_returns.index)

    rf_period = _align_rf_to_period(rf_series, period_returns.index, ppy)
    excess = period_returns - rf_period

    cum = (1 + period_returns).prod()
    n_years = len(period_returns) / ppy
    ann_ret = cum ** (1 / max(n_years, 0.01)) - 1 if n_years > 0 else 0.0
    ann_vol = period_returns.std() * np.sqrt(ppy)

    # Sharpe
    if ann_vol > 0 and len(excess) > 1:
        sharpe = (excess.mean() * ppy) / ann_vol
    else:
        sharpe = 0.0

    # Sortino -- downside deviation of EXCESS returns
    downside_excess = excess.clip(upper=0.0)
    dd_squared = (downside_excess ** 2).mean()
    downside_dev_ann = (
        np.sqrt(dd_squared) * np.sqrt(ppy) if dd_squared > 0 else np.inf
    )
    sortino = (
        (excess.mean() * ppy) / downside_dev_ann
        if downside_dev_ann > 0 and downside_dev_ann != np.inf
        else 0.0
    )

    # Drawdown
    cum_ret = (1 + period_returns).cumprod()
    rolling_max = cum_ret.cummax()
    drawdowns = cum_ret / rolling_max - 1
    max_dd = drawdowns.min()
    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 0 else 0.0

    dd_duration = _compute_max_drawdown_duration(cum_ret)

    win_rate = (period_returns > 0).mean()
    pct_negative = (period_returns < 0).mean()
    skew = period_returns.skew() if len(period_returns) > 2 else 0.0
    kurt = period_returns.kurtosis() if len(period_returns) > 3 else 0.0
    worst = period_returns.min()
    best = period_returns.max()

    hvar_99 = (
        -np.percentile(period_returns.values, 1)
        if len(period_returns) > 10
        else np.nan
    )
    var_threshold = (
        np.percentile(period_returns.values, 1)
        if len(period_returns) > 10
        else np.nan
    )
    if not np.isnan(var_threshold):
        tail = period_returns[period_returns <= var_threshold]
        cvar_99 = -tail.mean() if len(tail) > 0 else np.nan
    else:
        cvar_99 = np.nan

    ann_turnover = np.nan
    if turnovers and len(turnovers) > 0:
        ann_turnover = np.mean(turnovers) * ppy

    metrics = {
        "annualised_return": ann_ret,
        "annualised_volatility": ann_vol,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown": max_dd,
        "max_drawdown_duration_periods": dd_duration,
        "calmar_ratio": calmar,
        "win_rate": win_rate,
        "pct_negative_periods": pct_negative,
        "total_return": cum - 1,
        "skewness": skew,
        "kurtosis": kurt,
        "worst_period": worst,
        "best_period": best,
        "hvar_99": hvar_99,
        "cvar_99": cvar_99,
        "annualised_turnover": ann_turnover,
        "n_periods": len(period_returns),
        "periods_per_year": ppy,
    }

    if benchmark_returns is not None and len(benchmark_returns) > 1:
        aligned = pd.DataFrame(
            {"port": period_returns, "bench": benchmark_returns}
        ).dropna()
        if len(aligned) > 1:
            active = aligned["port"] - aligned["bench"]
            te = active.std() * np.sqrt(ppy)
            ir = (active.mean() * ppy) / te if te > 0 else 0.0
            metrics["information_ratio"] = ir
            metrics["tracking_error"] = te
            bench_cum = (1 + aligned["bench"]).prod()
            metrics["benchmark_return"] = bench_cum - 1

            cov_pb = aligned["port"].cov(aligned["bench"])
            var_b = aligned["bench"].var()
            beta = cov_pb / var_b if var_b > 0 else 0.0
            alpha = (
                aligned["port"].mean() - beta * aligned["bench"].mean()
            ) * ppy
            metrics["beta"] = beta
            metrics["alpha"] = alpha

    return metrics


def _compute_max_drawdown_duration(cum_ret: pd.Series) -> int:
    """Maximum number of periods from peak to next new high."""
    rolling_max = cum_ret.cummax()
    in_drawdown = cum_ret < rolling_max
    max_duration = 0
    current_duration = 0
    for is_dd in in_drawdown:
        if is_dd:
            current_duration += 1
            max_duration = max(max_duration, current_duration)
        else:
            current_duration = 0
    return max_duration


def _align_rf_to_period(
    rf_series: pd.Series, dates: pd.DatetimeIndex, ppy: float
) -> pd.Series:
    """Convert annualised rf (%) to per-period rate, aligned to return dates."""
    if rf_series is None or len(rf_series) == 0:
        return pd.Series(0.0, index=dates)
    rf_ann = rf_series / 100.0
    rf_vals = []
    for d in dates:
        mask = rf_ann.index <= d
        if mask.any():
            annual = rf_ann.loc[mask].iloc[-1]
        else:
            annual = 0.0
        period_rate = (1 + annual) ** (1 / ppy) - 1
        rf_vals.append(period_rate)
    return pd.Series(rf_vals, index=dates)


def build_tearsheet(
    results: dict,
    monthly_gross: pd.Series,
    monthly_net: pd.Series,
    rf_series: pd.Series,
    benchmark_returns: pd.Series = None,
) -> dict:
    """Build a tearsheet dict with gross and net metrics."""
    turnovers = results.get("turnovers", [])
    gross_metrics = compute_performance_metrics(
        monthly_gross, rf_series, benchmark_returns, turnovers
    )
    net_metrics = compute_performance_metrics(
        monthly_net, rf_series, benchmark_returns, turnovers
    )
    return {
        "gross": gross_metrics,
        "net": net_metrics,
        "monthly_gross": monthly_gross,
        "monthly_net": monthly_net,
        "turnovers": turnovers,
        "var_scales": results.get("var_scales", []),
    }
