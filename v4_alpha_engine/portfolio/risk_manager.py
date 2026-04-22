"""
portfolio/risk_manager.py -- Risk management for v4 Alpha Engine.

All constraints applied as FINAL step, after everything else:
  1. enforce_max_weight(5%) -- ABSOLUTE cap, no exceptions
  2. var_position_scale -- scale to target daily VaR
  3. check_beta_and_adjust -- if |beta| > 0.15, scale the offending leg
  4. check_gross_leverage -- cap at 2.5x
"""

import pandas as pd
import numpy as np


def var_position_scale(
    cumulative_daily_returns: pd.Series,
    config: dict,
) -> float:
    """Compute position scale factor based on HVaR.

    Returns 1.0 during burn-in period (insufficient data).
    """
    target_risk = config.get("target_risk", 0.02)
    confidence = config.get("var_confidence", 0.99)
    window = config.get("var_window_days", 756)
    burn_in = config.get("var_burn_in_days", 756)

    r = cumulative_daily_returns.dropna()
    n = len(r)
    if n < burn_in:
        return 1.0

    tail = r.tail(window)
    if len(tail) < 10:
        return 1.0

    alpha = 1 - confidence
    var = -np.percentile(tail.values, alpha * 100)
    var = max(var, 1e-6)

    scale = target_risk / var
    return min(scale, 2.0)  # cap at 2x


def enforce_max_weight(weights: pd.Series, max_weight: float = 0.05) -> pd.Series:
    """ABSOLUTE cap on individual position weights. No exceptions.

    FIX (Codex audit #5): enforced AFTER all scaling (VaR, beta).
    Called THREE TIMES in the engine: after construction, after VaR, after beta.
    """
    if len(weights) == 0:
        return weights

    clipped = weights.clip(lower=-max_weight, upper=max_weight)

    # Redistribute excess proportionally within each leg
    for mask_fn in [lambda w: w > 0, lambda w: w < 0]:
        mask = mask_fn(weights)
        if not mask.any():
            continue

        orig = weights[mask].sum()
        new = clipped[mask].sum()
        excess = orig - new

        if abs(excess) < 1e-10:
            continue

        uncapped = mask_fn(clipped) & (clipped.abs() < max_weight - 1e-8)
        if uncapped.any():
            unc_sum = clipped[uncapped].sum()
            if abs(unc_sum) > 1e-10:
                prop = clipped[uncapped] / unc_sum
                clipped[uncapped] += excess * prop

    # Final safety clip
    return clipped.clip(lower=-max_weight, upper=max_weight)


def check_beta_and_adjust(
    weights: pd.Series,
    daily_ret_wide: pd.DataFrame,
    benchmark_ret: pd.Series,
    as_of: pd.Timestamp,
    max_beta: float = 0.15,
    lookback: int = 252,
) -> pd.Series:
    """If |portfolio_beta| > max_beta, scale down the offending leg.

    FIX (Codex audit): scale the LEG that causes beta drift, not the whole portfolio.
    """
    if len(weights) == 0:
        return weights

    long_w = weights[weights > 0]
    short_w = weights[weights < 0]

    if len(long_w) == 0 or len(short_w) == 0:
        return weights

    # Get trailing returns
    ret = daily_ret_wide.loc[daily_ret_wide.index <= as_of].iloc[-lookback:]
    bm = benchmark_ret.reindex(ret.index).dropna()
    common_dates = ret.index.intersection(bm.index)

    if len(common_dates) < 60:
        return weights

    ret = ret.loc[common_dates]
    bm = bm.loc[common_dates]
    bm_var = bm.var()

    if bm_var < 1e-10:
        return weights

    # Compute individual stock betas
    betas = pd.Series(1.0, index=weights.index)
    for sym in weights.index:
        if sym in ret.columns:
            stock = ret[sym].dropna()
            common = stock.index.intersection(bm.index)
            if len(common) > 60:
                cov = np.cov(stock.loc[common].values, bm.loc[common].values)[0, 1]
                betas[sym] = cov / bm_var

    # Portfolio beta
    port_beta = (weights * betas).sum()

    if abs(port_beta) <= max_beta:
        return weights

    # Scale the offending leg
    adjusted = weights.copy()

    if port_beta > max_beta:
        # Too much long beta -> scale down longs
        long_beta = (long_w * betas.reindex(long_w.index, fill_value=1.0)).sum()
        short_beta = (short_w * betas.reindex(short_w.index, fill_value=1.0)).sum()
        target_long_beta = max_beta - short_beta
        if long_beta > 0:
            scale = max(0.3, target_long_beta / long_beta)
            adjusted[adjusted > 0] *= scale
    elif port_beta < -max_beta:
        # Too much short beta -> scale down shorts
        long_beta = (long_w * betas.reindex(long_w.index, fill_value=1.0)).sum()
        short_beta = (short_w * betas.reindex(short_w.index, fill_value=1.0)).sum()
        target_short_beta = -max_beta - long_beta
        if short_beta < 0:
            scale = max(0.3, target_short_beta / short_beta)
            adjusted[adjusted < 0] *= scale

    return adjusted


def check_gross_leverage(
    weights: pd.Series,
    max_gross: float = 2.5,
) -> pd.Series:
    """Cap gross leverage at max_gross."""
    if len(weights) == 0:
        return weights

    gross = weights.abs().sum()
    if gross <= max_gross:
        return weights

    return weights * (max_gross / gross)


def final_neutrality_projection(
    weights: pd.Series,
    sectors: pd.Series,
    daily_ret_wide: pd.DataFrame,
    benchmark_ret: pd.Series,
    as_of: pd.Timestamp,
    max_weight: float = 0.05,
    max_net: float = 0.02,
    max_sector_net: float = 0.02,
    max_beta: float = 0.10,
    max_gross: float = 2.5,
) -> pd.Series:
    """FINAL hard projection — called LAST, after everything else.

    Enforces in order:
    1. Net exposure → 0 (within max_net)
    2. Sector net → 0 (within max_sector_net per sector)
    3. Beta → 0 (within max_beta)
    4. Max weight (absolute 5%)
    5. Gross leverage cap

    This is the ONLY place where market-neutral is guaranteed.
    All upstream processing (NTB, VaR, sentiment mult) can break neutrality;
    this function fixes it as a hard constraint.
    """
    if len(weights) == 0:
        return weights

    w = weights.copy()

    # 1. Net exposure → 0
    net = w.sum()
    if abs(net) > max_net:
        # Scale down the larger leg
        long_sum = w[w > 0].sum()
        short_sum = w[w < 0].sum()
        if net > 0 and long_sum > 0:
            # Too long → scale down longs
            target_long = long_sum - net
            w[w > 0] *= max(0.5, target_long / long_sum)
        elif net < 0 and short_sum < 0:
            # Too short → scale down shorts
            target_short = short_sum - net
            w[w < 0] *= max(0.5, target_short / short_sum)

    # 2. Sector net → 0
    sec = sectors.reindex(w.index).dropna()
    for sector in sec.unique():
        mask = sec == sector
        sector_w = w[mask]
        sector_net = sector_w.sum()
        if abs(sector_net) > max_sector_net:
            long_mask = mask & (w > 0)
            short_mask = mask & (w < 0)
            if sector_net > 0 and long_mask.any():
                excess = sector_net - max_sector_net
                long_sum = w[long_mask].sum()
                if long_sum > 0:
                    w[long_mask] *= max(0.3, (long_sum - excess) / long_sum)
            elif sector_net < 0 and short_mask.any():
                excess = abs(sector_net) - max_sector_net
                short_sum = abs(w[short_mask].sum())
                if short_sum > 0:
                    w[short_mask] *= max(0.3, (short_sum - excess) / short_sum)

    # 3. Max weight
    w = w.clip(lower=-max_weight, upper=max_weight)

    # 4. Gross leverage
    gross = w.abs().sum()
    if gross > max_gross:
        w *= max_gross / gross

    # 5. Re-check net after all adjustments
    net = w.sum()
    if abs(net) > max_net:
        # Tiny proportional adjustment to both legs
        long_sum = w[w > 0].sum()
        short_sum = abs(w[w < 0].sum())
        if long_sum > 0 and short_sum > 0:
            avg = (long_sum + short_sum) / 2
            w[w > 0] *= avg / long_sum
            w[w < 0] *= avg / short_sum

    return w
