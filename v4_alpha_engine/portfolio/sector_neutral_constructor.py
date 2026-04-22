"""
portfolio/sector_neutral_constructor.py -- Sector-neutral portfolio construction.

Core innovation: selection happens WITHIN each sector, not across the whole universe.
This guarantees sector neutrality by construction (long_notional ~ short_notional per sector).

Steps:
  1. Within each GICS sector, rank by alpha_score
  2. Long = top 20% that pass quality_long gate
  3. Short = bottom 20% that pass quality_short gate
  4. Match long_notional = short_notional per sector
  5. Apply sentiment conviction multipliers
  6. Apply no-trade band (keep prev holdings if still in buffer)
  7. Max weight 5% enforced (first pass; risk_manager enforces again later)
"""

import pandas as pd
import numpy as np


def construct_sector_neutral_portfolio(
    alpha_scores: pd.Series,
    quality_long: pd.Series,
    quality_short: pd.Series,
    sentiment_conv: pd.DataFrame,
    sectors: pd.Series,
    daily_ret_wide: pd.DataFrame,
    as_of: pd.Timestamp,
    config: dict,
    prev_weights: pd.Series = None,
) -> pd.Series:
    """Build sector-neutral long/short portfolio.

    Parameters
    ----------
    alpha_scores : composite alpha z-score per symbol
    quality_long : bool Series, True = long-eligible
    quality_short : bool Series, True = short-eligible
    sentiment_conv : DataFrame with symbol, long_mult, short_mult
    sectors : symbol -> GICS sector
    daily_ret_wide : for covariance estimation
    as_of : rebalance date
    config : portfolio config block
    prev_weights : previous period weights (for no-trade band)

    Returns
    -------
    pd.Series of weights (positive=long, negative=short)
    """
    port_cfg = config
    long_pct = port_cfg.get("long_pct", 0.20)
    short_pct = port_cfg.get("short_pct", 0.20)
    max_weight = port_cfg.get("max_weight", 0.05)
    max_imbalance = port_cfg.get("max_sector_imbalance", 0.02)
    cov_window = port_cfg.get("cov_window", 252)

    # Only work with symbols that have alpha scores and sector info
    valid = alpha_scores.dropna().index.intersection(sectors.dropna().index)
    alpha = alpha_scores.reindex(valid)
    sec = sectors.reindex(valid)

    all_long = []
    all_short = []

    for sector_name, group_idx in sec.groupby(sec).groups.items():
        sector_alpha = alpha.reindex(group_idx).dropna().sort_values(ascending=False)
        n = len(sector_alpha)
        if n < 5:
            continue  # skip tiny sectors

        n_long = max(1, int(np.ceil(n * long_pct)))
        n_short = max(1, int(np.ceil(n * short_pct)))

        # Top = long candidates, bottom = short candidates
        long_cands = list(sector_alpha.head(n_long).index)
        short_cands = list(sector_alpha.tail(n_short).index)

        # Apply quality gate
        long_pass = [
            s for s in long_cands
            if quality_long.get(s, True)
        ]
        short_pass = [
            s for s in short_cands
            if quality_short.get(s, False)
        ]

        # Need at least 1 on each side for sector pair
        if len(long_pass) == 0 or len(short_pass) == 0:
            continue

        all_long.extend(long_pass)
        all_short.extend(short_pass)

    if len(all_long) == 0 and len(all_short) == 0:
        return pd.Series(dtype=float)

    # ---- Compute inverse-variance weights within each leg ----
    weights = _compute_inv_var_weights(
        all_long, all_short, daily_ret_wide, as_of, cov_window
    )

    # ---- Apply sentiment conviction multipliers ----
    if sentiment_conv is not None and len(sentiment_conv) > 0:
        sent_map = sentiment_conv.set_index("symbol")
        for sym in weights.index:
            if sym not in sent_map.index:
                continue
            w = weights[sym]
            if w > 0:
                mult = sent_map.loc[sym, "long_mult"]
                weights[sym] = w * mult
            elif w < 0:
                mult = sent_map.loc[sym, "short_mult"]
                weights[sym] = w * mult

        # Re-normalise each leg to maintain dollar neutrality
        long_mask = weights > 0
        short_mask = weights < 0
        long_sum = weights[long_mask].sum()
        short_sum = weights[short_mask].sum()
        target_notional = 1.0

        if long_sum > 0:
            weights[long_mask] *= target_notional / long_sum
        if short_sum < 0:
            weights[short_mask] *= target_notional / abs(short_sum)

    # ---- Enforce sector balance ----
    weights = _enforce_sector_balance(weights, sectors, max_imbalance)

    # ---- No-trade band ----
    ntb_cfg = config.get("_ntb_config", {})
    if ntb_cfg.get("enabled", False) and prev_weights is not None and len(prev_weights) > 0:
        weights = _apply_no_trade_band(
            weights, prev_weights, alpha_scores, sectors,
            long_pct, short_pct, ntb_cfg.get("buffer_pct", 0.05),
            quality_long, quality_short,
        )

    # ---- First-pass max weight enforcement ----
    weights = _enforce_max_weight(weights, max_weight)

    return weights


def _compute_inv_var_weights(
    long_syms: list,
    short_syms: list,
    daily_ret_wide: pd.DataFrame,
    as_of: pd.Timestamp,
    cov_window: int,
) -> pd.Series:
    """Compute inverse-variance weights for long and short legs separately.

    Uses trailing volatility to weight positions (lower vol -> higher weight).
    Normalises each leg to sum to 1.0 (long) and -1.0 (short).
    """
    ret = daily_ret_wide.loc[daily_ret_wide.index <= as_of].iloc[-cov_window:]

    def _leg_weights(syms, sign):
        available = [s for s in syms if s in ret.columns]
        if len(available) == 0:
            return pd.Series(dtype=float)

        vol = ret[available].std().replace(0, np.nan)
        vol = vol.dropna()
        if len(vol) == 0:
            return pd.Series(sign / max(len(syms), 1), index=syms)

        inv_vol = 1.0 / vol
        w = inv_vol / inv_vol.sum()

        # Fill missing with equal weight
        all_w = pd.Series(0.0, index=syms)
        missing = [s for s in syms if s not in w.index]
        if len(missing) > 0:
            equal = (1.0 / len(syms))
            for s in missing:
                all_w[s] = equal
            remaining = 1.0 - equal * len(missing)
            for s in w.index:
                all_w[s] = w[s] * remaining
        else:
            for s in w.index:
                all_w[s] = w[s]

        return all_w * sign

    long_w = _leg_weights(long_syms, 1.0)
    short_w = _leg_weights(short_syms, -1.0)

    all_syms = set(long_syms) | set(short_syms)
    weights = pd.Series(0.0, index=sorted(all_syms))
    for s in long_w.index:
        weights[s] = long_w[s]
    for s in short_w.index:
        weights[s] = short_w[s]

    return weights


def _enforce_sector_balance(
    weights: pd.Series,
    sectors: pd.Series,
    max_imbalance: float,
) -> pd.Series:
    """Ensure |long_notional - short_notional| per sector <= max_imbalance."""
    adj = weights.copy()

    for sector in sectors.unique():
        sec_syms = [s for s in adj.index if sectors.get(s, "") == sector]
        if not sec_syms:
            continue

        long_syms = [s for s in sec_syms if adj[s] > 0]
        short_syms = [s for s in sec_syms if adj[s] < 0]

        long_not = sum(adj[s] for s in long_syms)
        short_not = sum(abs(adj[s]) for s in short_syms)

        if long_not == 0 or short_not == 0:
            continue

        imbalance = abs(long_not - short_not)
        if imbalance > max_imbalance:
            # Scale the larger leg down to match the smaller
            target = min(long_not, short_not)
            if long_not > short_not:
                scale = target / long_not
                for s in long_syms:
                    adj[s] *= scale
            else:
                scale = target / short_not
                for s in short_syms:
                    adj[s] *= scale  # adj[s] is negative, scaling preserves sign

    return adj


def _enforce_max_weight(weights: pd.Series, max_weight: float) -> pd.Series:
    """Clip weights to [-max_weight, max_weight] and redistribute excess."""
    if len(weights) == 0:
        return weights

    clipped = weights.clip(lower=-max_weight, upper=max_weight)

    # Redistribute clipped excess within each leg
    for sign, mask_fn in [
        (1, lambda w: w > 0),
        (-1, lambda w: w < 0),
    ]:
        mask = mask_fn(weights)
        if not mask.any():
            continue

        orig_total = weights[mask].sum()
        new_total = clipped[mask].sum()
        excess = orig_total - new_total

        if abs(excess) < 1e-10:
            continue

        uncapped = mask_fn(clipped) & (clipped.abs() < max_weight - 1e-8)
        if uncapped.any() and abs(clipped[uncapped].sum()) > 1e-10:
            # Redistribute proportionally
            prop = clipped[uncapped] / clipped[uncapped].sum()
            clipped[uncapped] += excess * prop

    # Final clip to be safe
    clipped = clipped.clip(lower=-max_weight, upper=max_weight)
    return clipped


def _apply_no_trade_band(
    new_weights: pd.Series,
    prev_weights: pd.Series,
    alpha_scores: pd.Series,
    sectors: pd.Series,
    long_pct: float,
    short_pct: float,
    buffer_pct: float,
    quality_long: pd.Series,
    quality_short: pd.Series,
) -> pd.Series:
    """Keep previous holdings if still within buffer zone.

    If a stock was held previously and its alpha rank is within the buffer
    (e.g., top 25% instead of strict top 20%), keep it.
    """
    adjusted = new_weights.copy()

    # For each sector, check if previously held stocks are in the buffer zone
    for sector in sectors.unique():
        sec_syms = [s for s in alpha_scores.dropna().index if sectors.get(s, "") == sector]
        if len(sec_syms) < 5:
            continue

        sec_alpha = alpha_scores.reindex(sec_syms).dropna().sort_values(ascending=False)
        n = len(sec_alpha)

        # Buffer zone
        n_long_wide = max(1, int(np.ceil(n * (long_pct + buffer_pct))))
        n_short_wide = max(1, int(np.ceil(n * (short_pct + buffer_pct))))

        wider_long = set(sec_alpha.head(n_long_wide).index)
        wider_short = set(sec_alpha.tail(n_short_wide).index)

        # Re-include previously held stocks that are in the wider band
        for sym in prev_weights.index:
            if sectors.get(sym, "") != sector:
                continue

            prev_w = prev_weights.get(sym, 0)
            curr_w = adjusted.get(sym, 0)

            if prev_w > 0 and curr_w <= 0 and sym in wider_long:
                # Was long, dropped out, but still in buffer
                if quality_long.get(sym, True):
                    adjusted[sym] = prev_w * 0.8  # slightly reduced
            elif prev_w < 0 and curr_w >= 0 and sym in wider_short:
                # Was short, dropped out, but still in buffer
                if quality_short.get(sym, False):
                    adjusted[sym] = prev_w * 0.8

    return adjusted
