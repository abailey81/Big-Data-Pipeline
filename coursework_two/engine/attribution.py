"""Factor-return attribution: Fama-MacBeth + Kyle's-λ capacity.

Engine-side math for PLAN §5.9 and §5.11.  Analytics-layer consumes the
resulting Parquet artefacts (``factor_premia.parquet``, capacity JSON).

Fama-MacBeth (§5.9)
-------------------
At each rebalance t, run cross-sectional OLS of next-month returns on
z-scored factors:
    r_{i, t+1} = β_{mom,t} · z_{mom,i,t} + β_{val,t} · z_{val,i,t} + ...
Collect β_f time-series; report mean(β_f), Fama-MacBeth t-stat, and R².

Kyle's-λ capacity (§5.11)
-------------------------
Amihud (2002) illiquidity = mean(|r| / $-volume).  Kyle's λ ≈ Amihud.
Per-name predicted impact at Q = λ · Q.  Capacity = max AUM such that
|w_i · AUM · λ_i| ≤ 15 bp (Kyle 1985; Almgren-Chriss 2001).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


# =============================================================================
# Fama-MacBeth per-factor premium (monthly cross-sectional OLS + t-stat)
# =============================================================================
def fama_macbeth_one_date(
    z_scores: pd.DataFrame, forward_returns: pd.Series
) -> dict[str, float]:
    """Run one cross-sectional OLS:  r_{i,t+1} = Z_i β + ε."""
    df = z_scores.join(forward_returns.rename("ret"), how="inner").dropna()
    if len(df) < 10:
        return {}
    X = df.drop(columns=["ret"]).values
    y = df["ret"].values
    # Add constant?  Fama-MacBeth standard omits it (returns mean is part of factor premia)
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return {}
    residuals = y - X @ beta
    dof = max(1, len(y) - X.shape[1])
    # HC0 SE for t-stats
    try:
        XtX_inv = np.linalg.inv(X.T @ X)
        sigma_sq = float((residuals @ residuals) / dof)
        var_beta = sigma_sq * np.diag(XtX_inv)
        se = np.sqrt(np.maximum(var_beta, 0))
    except np.linalg.LinAlgError:
        se = np.full(X.shape[1], np.nan)
    y_var = float(((y - y.mean()) ** 2).sum())
    if y_var == 0:
        r2 = np.nan
    else:
        r2 = 1 - float((residuals ** 2).sum()) / y_var

    out = {}
    for i, f in enumerate(df.drop(columns=["ret"]).columns):
        out[f] = float(beta[i])
        out[f"t_{f}"] = float(beta[i] / se[i]) if se[i] > 0 else np.nan
    out["r_squared"] = r2
    out["n"] = len(df)
    return out


def fama_macbeth_t_stat(beta_series: pd.Series) -> tuple[float, float, int]:
    """Mean / (stdev / √T) — Fama-MacBeth (1973) aggregate t-stat."""
    b = beta_series.dropna()
    if len(b) < 3:
        return float("nan"), float("nan"), len(b)
    mean = float(b.mean())
    se = float(b.std(ddof=1) / np.sqrt(len(b)))
    t = mean / se if se > 0 else float("nan")
    return mean, t, len(b)


# =============================================================================
# Amihud illiquidity and Kyle's-λ capacity
# =============================================================================
def amihud_illiquidity(
    daily_returns: pd.DataFrame, daily_dollar_volume: pd.DataFrame
) -> pd.Series:
    """Amihud (2002) illiquidity = mean(|R_i| / $-volume_i).

    Returns a per-symbol value in units of 1/$ (return per $ traded).
    """
    abs_ret = daily_returns.abs()
    dv = daily_dollar_volume.replace(0, np.nan)
    ill = (abs_ret / dv).mean(axis=0)
    return ill


def capacity_bps_per_aum(
    weights: pd.Series,
    amihud_ill: pd.Series,
    adv_dollar: pd.Series,
) -> pd.DataFrame:
    """For each target AUM, estimate per-name market impact.

    impact_bp_i = |w_i · AUM| · λ_i · 10_000
    where λ_i (per $) ≈ Amihud illiquidity_i.
    """
    merged = pd.concat(
        [weights.rename("w"), amihud_ill.rename("amihud"), adv_dollar.rename("adv")], axis=1
    ).dropna()
    merged["lambda_per_dollar"] = merged["amihud"]
    return merged


def max_aum_at_impact_budget(
    weights: pd.Series,
    amihud_ill: pd.Series,
    impact_budget_bp: float = 15.0,
) -> float:
    """Solve for largest AUM such that max per-name impact ≤ impact_budget_bp.

    per-name impact(Q) = |w| · AUM · λ · 10_000 ≤ 15 bp
    ⇒ AUM ≤ 15 / (|w| · λ · 10_000)  minimum across names
    """
    merged = pd.concat(
        [weights.rename("w"), amihud_ill.rename("lam")], axis=1
    ).dropna()
    merged = merged[(merged["w"].abs() > 1e-6) & (merged["lam"] > 0)]
    if merged.empty:
        return float("inf")
    per_name_max = impact_budget_bp / (merged["w"].abs() * merged["lam"] * 10_000.0)
    return float(per_name_max.min())


@dataclass
class CapacityEstimator:
    """Convenience object: capacity at impact budget plus sensitivity bands."""

    def estimate(
        self,
        weights: pd.Series,
        daily_returns: pd.DataFrame,
        daily_dollar_volume: pd.DataFrame,
        impact_budget_bp: float = 15.0,
    ) -> dict:
        ill = amihud_illiquidity(daily_returns, daily_dollar_volume)
        aum_capacity = max_aum_at_impact_budget(weights, ill, impact_budget_bp)
        return {
            "impact_budget_bp": impact_budget_bp,
            "capacity_usd": aum_capacity,
            "n_stocks_assessed": int((weights.abs() > 1e-6).sum()),
            "median_amihud": float(ill.median()),
        }


__all__ = [
    "CapacityEstimator",
    "amihud_illiquidity",
    "capacity_bps_per_aum",
    "fama_macbeth_one_date",
    "fama_macbeth_t_stat",
    "max_aum_at_impact_budget",
]
