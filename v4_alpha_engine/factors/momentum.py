"""
factors/momentum.py -- Multi-horizon residual momentum (PRIMARY alpha, 45%).

Computes:
  - 12-1 month momentum (weight 0.35)
  - 6-1 month momentum  (weight 0.30)
  - 3-1 month momentum  (weight 0.20)
  - Residual momentum    (weight 0.15): beta-adjusted via market regression

All sector-neutral z-scored with 2.5/97.5 winsorisation.
Returns composite momentum score (pd.Series).
"""

import pandas as pd
import numpy as np
from factors.z_scoring import sector_neutral_zscore


def compute_multi_horizon_momentum(
    price_matrix: pd.DataFrame,
    daily_ret_wide: pd.DataFrame,
    benchmark_ret: pd.Series,
    sectors: pd.Series,
    as_of: pd.Timestamp,
    config: dict,
) -> pd.Series:
    """Compute multi-horizon + residual momentum composite.

    Parameters
    ----------
    price_matrix : date x symbol adj_close matrix
    daily_ret_wide : date x symbol daily USD returns
    benchmark_ret : daily S&P 500 returns
    sectors : symbol -> GICS sector mapping
    as_of : rebalance date
    config : factors.momentum config block

    Returns
    -------
    pd.Series of composite momentum z-scores, indexed by symbol.
    """
    horizons = config.get("horizons", {})
    components = []
    total_weight = 0.0

    # FIX: Use USD daily returns for momentum, not local currency prices.
    # This ensures signal and P&L are in the same currency (USD).
    for key, h_cfg in horizons.items():
        if key == "residual":
            continue
        lookback = h_cfg["lookback"]
        skip = h_cfg["skip"]
        weight = h_cfg["weight"]
        raw = _compute_usd_momentum(daily_ret_wide, as_of, lookback, skip)
        z = sector_neutral_zscore(raw, sectors)
        components.append((z, weight))
        total_weight += weight

    # Residual momentum
    res_cfg = horizons.get("residual", {})
    res_weight = res_cfg.get("weight", 0.15)
    if res_weight > 0:
        raw_res = _compute_residual_momentum(
            daily_ret_wide, benchmark_ret, as_of,
            lookback_days=252, mom_lookback=12, mom_skip=1,
        )
        z_res = sector_neutral_zscore(raw_res, sectors)
        components.append((z_res, res_weight))
        total_weight += res_weight

    if not components or total_weight == 0:
        return pd.Series(dtype=float, name="momentum")

    # Weighted combination
    all_syms = set()
    for z, _ in components:
        all_syms.update(z.dropna().index)
    all_syms = sorted(all_syms)

    composite = pd.Series(0.0, index=all_syms)
    weight_sum = pd.Series(0.0, index=all_syms)

    for z, w in components:
        vals = z.reindex(all_syms)
        mask = vals.notna()
        composite[mask] += vals[mask] * w
        weight_sum[mask] += w

    # Normalise by available weights (NaN-safe)
    valid = weight_sum > 0
    composite[valid] = composite[valid] / weight_sum[valid]
    composite[~valid] = np.nan
    composite.name = "momentum"
    return composite


def _compute_usd_momentum(
    daily_ret_wide: pd.DataFrame,
    as_of: pd.Timestamp,
    lookback_months: int,
    skip_months: int,
) -> pd.Series:
    """Compute cumulative USD return over [as_of - lookback, as_of - skip].

    FIX: Uses USD daily returns instead of local currency prices.
    This ensures momentum signal is in the same currency as P&L.
    """
    end_date = as_of - pd.DateOffset(months=skip_months)
    start_date = as_of - pd.DateOffset(months=lookback_months)

    mask = (daily_ret_wide.index >= start_date) & (daily_ret_wide.index <= end_date)
    period = daily_ret_wide.loc[mask]

    if len(period) < 10:
        return pd.Series(dtype=float)

    cum_ret = (1 + period.fillna(0)).prod() - 1
    # Require at least 50% non-NaN days
    valid_pct = period.notna().mean()
    cum_ret[valid_pct < 0.5] = np.nan
    cum_ret = cum_ret.replace([np.inf, -np.inf], np.nan)
    return cum_ret


def _compute_price_momentum(
    price_matrix: pd.DataFrame,
    as_of: pd.Timestamp,
    lookback_months: int,
    skip_months: int,
) -> pd.Series:
    """DEPRECATED: kept for reference. Use _compute_usd_momentum instead."""
    end_date = as_of - pd.DateOffset(months=skip_months)
    start_date = as_of - pd.DateOffset(months=lookback_months)

    mask_start = price_matrix.index <= start_date
    mask_end = price_matrix.index <= end_date

    if not mask_start.any() or not mask_end.any():
        return pd.Series(dtype=float)

    p_start = price_matrix.loc[mask_start].iloc[-1]
    p_end = price_matrix.loc[mask_end].iloc[-1]

    mom = (p_end / p_start) - 1.0
    mom[(p_start.isna()) | (p_end.isna()) | (p_start <= 0)] = np.nan
    mom = mom.replace([np.inf, -np.inf], np.nan)
    return mom


def _compute_residual_momentum(
    daily_ret_wide: pd.DataFrame,
    benchmark_ret: pd.Series,
    as_of: pd.Timestamp,
    lookback_days: int = 252,
    mom_lookback: int = 12,
    mom_skip: int = 1,
) -> pd.Series:
    """Compute residual momentum: regress stock returns on market, take residual.

    For each stock:
      1. Regress past 252-day returns on S&P 500 returns -> get beta
      2. residual_t = stock_return_t - beta * market_return_t
      3. Compute cumulative residual return over [t-12, t-1] months

    This strips out market beta from the momentum signal.
    """
    # Get trailing returns window
    ret = daily_ret_wide.loc[daily_ret_wide.index <= as_of].iloc[-lookback_days:]
    if len(ret) < 60:
        return pd.Series(dtype=float, name="residual_momentum")

    # Align benchmark
    bm = benchmark_ret.reindex(ret.index).fillna(0)
    bm_var = bm.var()
    if bm_var < 1e-10:
        return pd.Series(dtype=float, name="residual_momentum")

    # Compute residuals for each stock
    residuals = pd.DataFrame(index=ret.index, columns=ret.columns, dtype=float)
    for sym in ret.columns:
        stock = ret[sym].fillna(0)
        common_mask = stock.notna() & bm.notna()
        if common_mask.sum() < 60:
            continue
        cov = np.cov(stock[common_mask].values, bm[common_mask].values)[0, 1]
        beta = cov / bm_var
        residuals[sym] = stock - beta * bm

    # Compute cumulative residual return over momentum window
    skip_days = int(mom_skip * 21)  # ~21 trading days per month
    lookback_days_mom = int(mom_lookback * 21)

    if len(residuals) <= skip_days:
        return pd.Series(dtype=float, name="residual_momentum")

    # Exclude most recent skip period
    if skip_days > 0:
        res_window = residuals.iloc[:-skip_days]
    else:
        res_window = residuals

    # Take the lookback period
    if len(res_window) > lookback_days_mom:
        res_window = res_window.iloc[-lookback_days_mom:]

    # Cumulative residual return
    cum_res = (1 + res_window.fillna(0)).prod() - 1
    cum_res = cum_res.replace([np.inf, -np.inf], np.nan)
    cum_res.name = "residual_momentum"
    return cum_res
