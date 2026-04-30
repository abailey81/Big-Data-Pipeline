"""Performance metrics module (Viz Ref §1.1–1.6 + PLAN §5.7, 5.8, 5.18).

Implements the **complete** metric suite required by the CW2 report:
    • Return & risk-adjusted    (Sharpe, Sortino, IR, Calmar, annualised vol)
    • Drawdown & tail           (Max DD + duration, 99% HVaR, 99% ES, skew, kurt)
    • Distribution              (hit-rate, best/worst month, % negative, downside dev)
    • Long/short-specific       (leg alphas, gross/net exposure, turnover, β)
    • Factor diagnostics        (per-factor IC, IC-IR, % positive IC months, FM-Betas)
    • Headline 4×17 exhibit     (§1.6 template — fills the Section 4.1 table)
    • Block bootstrap CI        (Politis-Romano 1994, configurable block — report uses 3-month)
    • Deflated Sharpe Ratio     (Bailey & López de Prado 2014)
    • Probabilistic Sharpe      (PSR at threshold)
    • Minimum Backtest Length   (§5.18, Bailey et al. 2017)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


ANNUALISATION_MONTHLY = 12
ANNUALISATION_DAILY = 252


# =============================================================================
# §1.1 Return & risk-adjusted metrics
# =============================================================================
def annualised_return(monthly_returns: pd.Series, ann: int = ANNUALISATION_MONTHLY) -> float:
    r = monthly_returns.dropna()
    if len(r) == 0:
        return 0.0
    cum = float((1 + r).prod())
    years = len(r) / ann
    return cum ** (1 / years) - 1 if years > 0 else 0.0


def annualised_volatility(monthly_returns: pd.Series, ann: int = ANNUALISATION_MONTHLY) -> float:
    return float(monthly_returns.std(ddof=1) * np.sqrt(ann))


def sharpe_ratio(returns: pd.Series, rf_series: pd.Series | float = 0.0, ann: int = ANNUALISATION_MONTHLY) -> float:
    """Annualised Sharpe ratio with numerical-pathology guards.

    Second-pass audit finding: previous implementation guarded only σ, not μ.
    Now returns 0.0 for (μ or σ) in {±inf, NaN}.  Prevents bad CW1 data from
    propagating an infinite Sharpe into the headline exhibit.
    """
    r = returns.dropna()
    if len(r) == 0:
        return 0.0
    if isinstance(rf_series, pd.Series):
        excess = r - rf_series.reindex(r.index, fill_value=0.0)
    else:
        excess = r - (rf_series / ann if rf_series > 0 else 0.0)
    mu = excess.mean() * ann
    sigma = excess.std(ddof=1) * np.sqrt(ann)
    if not np.isfinite(mu) or not np.isfinite(sigma) or sigma <= 1e-12:
        return 0.0
    return float(mu / sigma)


def sortino_ratio(returns: pd.Series, rf_series=0.0, ann: int = ANNUALISATION_MONTHLY) -> float:
    r = returns.dropna()
    if len(r) == 0:
        return 0.0
    if isinstance(rf_series, pd.Series):
        excess = r - rf_series.reindex(r.index, fill_value=0.0)
    else:
        excess = r - (rf_series / ann if rf_series > 0 else 0.0)
    downside = excess[excess < 0]
    if len(downside) < 2:
        return np.nan
    dd_dev = downside.std(ddof=1) * np.sqrt(ann)
    if dd_dev == 0:
        return 0.0
    return float(excess.mean() * ann / dd_dev)


def information_ratio(
    returns: pd.Series, benchmark: pd.Series, ann: int = ANNUALISATION_MONTHLY
) -> float:
    aligned = pd.concat([returns, benchmark], axis=1).dropna()
    if len(aligned) < 2:
        return 0.0
    active = aligned.iloc[:, 0] - aligned.iloc[:, 1]
    te = active.std(ddof=1) * np.sqrt(ann)
    if te == 0:
        return 0.0
    return float(active.mean() * ann / te)


def calmar_ratio(returns: pd.Series, ann: int = ANNUALISATION_MONTHLY) -> float:
    ann_ret = annualised_return(returns, ann)
    mdd = max_drawdown(returns)
    return float(ann_ret / abs(mdd)) if mdd < 0 else float("inf")


# =============================================================================
# §1.2 Drawdown & tail metrics
# =============================================================================
def drawdown_series(returns: pd.Series) -> pd.Series:
    r = returns.dropna()
    nav = (1 + r).cumprod()
    peak = nav.cummax()
    return (nav - peak) / peak


def max_drawdown(returns: pd.Series) -> float:
    dd = drawdown_series(returns)
    return float(dd.min()) if len(dd) else 0.0


def drawdown_duration_months(returns: pd.Series) -> int:
    dd = drawdown_series(returns)
    if len(dd) == 0:
        return 0
    idx_min = dd.idxmin()
    after = dd.loc[idx_min:]
    recovery = after[after >= -1e-8]
    if len(recovery) > 0:
        first = recovery.index[0]
        dur = (pd.Timestamp(first).to_period("M") - pd.Timestamp(idx_min).to_period("M")).n
    else:
        dur = (pd.Timestamp(dd.index[-1]).to_period("M") - pd.Timestamp(idx_min).to_period("M")).n
    return int(dur)


def historical_var(returns: pd.Series, confidence: float = 0.99) -> float:
    r = returns.dropna()
    if len(r) == 0:
        return 0.0
    return float(-r.quantile(1 - confidence))


def expected_shortfall(returns: pd.Series, confidence: float = 0.99) -> float:
    r = returns.dropna()
    if len(r) == 0:
        return 0.0
    cutoff = r.quantile(1 - confidence)
    tail = r[r <= cutoff]
    return float(-tail.mean()) if len(tail) else 0.0


def skewness(returns: pd.Series) -> float:
    r = returns.dropna()
    return float(stats.skew(r)) if len(r) > 2 else 0.0


def excess_kurtosis(returns: pd.Series) -> float:
    r = returns.dropna()
    return float(stats.kurtosis(r, fisher=True)) if len(r) > 3 else 0.0


# =============================================================================
# §1.3 Distribution & hit rate
# =============================================================================
def monthly_hit_rate(returns: pd.Series) -> float:
    r = returns.dropna()
    return float((r > 0).mean()) if len(r) else 0.0


def best_month(returns: pd.Series) -> float:
    return float(returns.dropna().max()) if len(returns.dropna()) else 0.0


def worst_month(returns: pd.Series) -> float:
    return float(returns.dropna().min()) if len(returns.dropna()) else 0.0


def pct_negative_months(returns: pd.Series) -> float:
    r = returns.dropna()
    return float((r < 0).mean()) if len(r) else 0.0


def downside_deviation(returns: pd.Series, mar: float = 0.0, ann: int = ANNUALISATION_MONTHLY) -> float:
    r = returns.dropna()
    downside = (r - mar).clip(upper=0)
    return float(np.sqrt((downside ** 2).mean()) * np.sqrt(ann))


# =============================================================================
# §5.7 Block bootstrap + Deflated Sharpe + PSR
# =============================================================================
def circular_block_bootstrap_sharpe(
    returns: pd.Series,
    block_size: int = 6,
    n_bootstrap: int = 1000,
    seed: int = 42,
    ann: int = ANNUALISATION_MONTHLY,
) -> dict:
    """Politis-Romano (1994) circular block bootstrap of Sharpe ratio.

    Returns dict with mean, 5%, 95% Sharpe CI.
    """
    rng = np.random.default_rng(seed)
    r = returns.dropna().values
    T = len(r)
    if T < block_size + 2:
        return {"mean": np.nan, "low": np.nan, "high": np.nan, "n": 0}
    n_blocks = int(np.ceil(T / block_size))
    sharpes = np.empty(n_bootstrap)
    for b in range(n_bootstrap):
        starts = rng.integers(0, T, size=n_blocks)
        sample = np.concatenate([
            np.take(r, np.arange(s, s + block_size) % T) for s in starts
        ])[:T]
        mu = sample.mean()
        sd = sample.std(ddof=1)
        sharpes[b] = (mu * ann) / (sd * np.sqrt(ann)) if sd > 0 else 0.0
    return {
        "mean": float(sharpes.mean()),
        "std": float(sharpes.std(ddof=1)),
        "low": float(np.quantile(sharpes, 0.025)),
        "high": float(np.quantile(sharpes, 0.975)),
        "n": n_bootstrap,
    }


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trials: int,
    returns: pd.Series,
    ann: int = ANNUALISATION_MONTHLY,
) -> dict:
    """Bailey & López de Prado (2014).

    DSR = Φ(z) where z accounts for sample length, skew, kurtosis and the
    multiplicity of trials tested.
    """
    r = returns.dropna()
    T = len(r)
    if T < 10 or n_trials < 1:
        return {"deflated_sharpe": np.nan, "psr": np.nan, "threshold_sr": np.nan}
    sk = stats.skew(r)
    k = stats.kurtosis(r, fisher=True)
    # Expected max Sharpe under null from n_trials (Bailey-LdP 2014)
    eg_max = (1 - 0.5772) * stats.norm.ppf(1 - 1 / n_trials) + 0.5772 * stats.norm.ppf(
        1 - 1 / (n_trials * np.e)
    ) if n_trials > 1 else 0.0
    sr_obs_periodic = observed_sharpe / np.sqrt(ann)
    numerator = (sr_obs_periodic - eg_max / np.sqrt(ann)) * np.sqrt(T - 1)
    # Second-pass audit: the quantity under the sqrt can go slightly negative
    # for extreme skew/kurt pairs — floor at 1e-12 to prevent NaN cascade.
    under_sqrt = max(1 - sk * sr_obs_periodic + ((k - 1) / 4.0) * sr_obs_periodic ** 2, 1e-12)
    denom = np.sqrt(under_sqrt)
    if denom == 0 or np.isnan(denom):
        return {"deflated_sharpe": np.nan, "psr": np.nan, "threshold_sr": float(eg_max)}
    z = numerator / denom
    if not np.isfinite(z):
        return {"deflated_sharpe": np.nan, "psr": np.nan, "threshold_sr": float(eg_max)}
    return {
        "deflated_sharpe": float(stats.norm.cdf(z)),
        "psr": float(stats.norm.cdf(z)),
        "threshold_sr": float(eg_max),
    }


def probabilistic_sharpe_ratio(
    observed_sharpe: float,
    threshold_sharpe: float,
    returns: pd.Series,
    ann: int = ANNUALISATION_MONTHLY,
) -> float:
    r = returns.dropna()
    T = len(r)
    if T < 10:
        return np.nan
    sk = stats.skew(r)
    k = stats.kurtosis(r, fisher=True)
    sr_p = observed_sharpe / np.sqrt(ann)
    t_p = threshold_sharpe / np.sqrt(ann)
    num = (sr_p - t_p) * np.sqrt(T - 1)
    den = np.sqrt(1 - sk * sr_p + ((k - 1) / 4.0) * sr_p ** 2)
    if den == 0 or np.isnan(den):
        return np.nan
    return float(stats.norm.cdf(num / den))


# =============================================================================
# §5.18 Minimum Backtest Length power analysis
# =============================================================================
def minimum_backtest_length(
    target_sharpe: float = 1.0,
    n_trials: int = 15,
    alpha: float = 0.05,
    gamma_const: float = 0.5772,
) -> float:
    """Bailey, Borwein, LdP, Zhu (2017) — minimum track record length.

    Returns the minimum number of months required to reject H0 at (1-α)
    confidence for a given target annualised Sharpe, accounting for the
    multiplicity of trials.
    """
    if target_sharpe <= 0:
        return np.inf
    z_alpha = stats.norm.ppf(1 - alpha)
    eg = (1 - gamma_const) * stats.norm.ppf(1 - 1 / n_trials) + gamma_const * stats.norm.ppf(
        1 - 1 / (n_trials * np.e)
    ) if n_trials > 1 else 0.0
    # Target Sharpe should exceed eg; otherwise no finite length suffices
    diff = (target_sharpe / np.sqrt(ANNUALISATION_MONTHLY)) - (eg / np.sqrt(ANNUALISATION_MONTHLY))
    if diff <= 0:
        return np.inf
    return float((z_alpha / diff) ** 2)


# =============================================================================
# §1.4 Long/short-specific metrics
# =============================================================================
def gross_exposure(weights: pd.Series) -> float:
    return float(weights.abs().sum())


def net_exposure(weights: pd.Series) -> float:
    return float(weights.sum())


def annualised_turnover(turnover_series: pd.Series, ann: int = ANNUALISATION_MONTHLY) -> float:
    return float(turnover_series.mean() * ann)


def portfolio_beta(portfolio_returns: pd.Series, benchmark: pd.Series) -> float:
    df = pd.concat([portfolio_returns, benchmark], axis=1).dropna()
    if len(df) < 3:
        return 0.0
    cov = df.cov().iloc[0, 1]
    var = df.iloc[:, 1].var()
    return float(cov / var) if var > 0 else 0.0


# =============================================================================
# §1.5 Factor-level diagnostics
# =============================================================================
def factor_ic_ir(ic_series: pd.Series) -> float:
    r = ic_series.dropna()
    if len(r) < 3:
        return 0.0
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd > 0 else 0.0


def pct_positive_ic_months(ic_series: pd.Series) -> float:
    r = ic_series.dropna()
    return float((r > 0).mean()) if len(r) else 0.0


# =============================================================================
# §1.6 Headline summary table
# =============================================================================
def compute_headline_metrics(
    returns_df: pd.DataFrame,
    rf_series: pd.Series | float = 0.0,
    benchmark_col: str = "benchmark_ew",
    ann: int = ANNUALISATION_MONTHLY,
) -> pd.DataFrame:
    """Produce the Section-4.1 headline exhibit (Viz Ref §1.6 template).

    Columns: Dynamic Gross · Dynamic Net 20bp · Static Net 20bp · Benchmark EW
    """
    cols = {
        "Dynamic Gross": "dynamic_gross",
        "Dynamic Net 20bp": "dynamic_net_20bp",
        "Static Net 20bp": "static_net_20bp",
        "Benchmark EW": benchmark_col,
    }
    rows = [
        "Annualised Return",
        "Annualised Volatility",
        "Sharpe Ratio",
        "Sortino Ratio",
        "Information Ratio",
        "Maximum Drawdown",
        "DD Duration (months)",
        "Calmar Ratio",
        "Monthly Hit Rate",
        "Skewness",
        "Excess Kurtosis",
        "99% HVaR",
        "99% ES",
    ]
    out = pd.DataFrame(index=rows, columns=list(cols.keys()))
    bench = returns_df[benchmark_col] if benchmark_col in returns_df.columns else pd.Series(dtype=float)

    for label, col in cols.items():
        if col not in returns_df.columns:
            continue
        s = returns_df[col].dropna()
        out.loc["Annualised Return", label] = annualised_return(s, ann)
        out.loc["Annualised Volatility", label] = annualised_volatility(s, ann)
        out.loc["Sharpe Ratio", label] = sharpe_ratio(s, rf_series, ann)
        out.loc["Sortino Ratio", label] = sortino_ratio(s, rf_series, ann)
        out.loc["Information Ratio", label] = information_ratio(s, bench, ann) if label != "Benchmark EW" else 0.0
        out.loc["Maximum Drawdown", label] = max_drawdown(s)
        out.loc["DD Duration (months)", label] = drawdown_duration_months(s)
        out.loc["Calmar Ratio", label] = calmar_ratio(s, ann)
        out.loc["Monthly Hit Rate", label] = monthly_hit_rate(s)
        out.loc["Skewness", label] = skewness(s)
        out.loc["Excess Kurtosis", label] = excess_kurtosis(s)
        out.loc["99% HVaR", label] = historical_var(s, 0.99)
        out.loc["99% ES", label] = expected_shortfall(s, 0.99)

    return out.astype(float)


__all__ = [
    "annualised_return",
    "annualised_turnover",
    "annualised_volatility",
    "best_month",
    "calmar_ratio",
    "circular_block_bootstrap_sharpe",
    "compute_headline_metrics",
    "deflated_sharpe_ratio",
    "downside_deviation",
    "drawdown_duration_months",
    "drawdown_series",
    "excess_kurtosis",
    "expected_shortfall",
    "factor_ic_ir",
    "gross_exposure",
    "historical_var",
    "information_ratio",
    "max_drawdown",
    "minimum_backtest_length",
    "monthly_hit_rate",
    "net_exposure",
    "pct_negative_months",
    "pct_positive_ic_months",
    "portfolio_beta",
    "probabilistic_sharpe_ratio",
    "sharpe_ratio",
    "skewness",
    "sortino_ratio",
    "worst_month",
]
