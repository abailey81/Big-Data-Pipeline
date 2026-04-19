"""
factors/value.py -- PIT-safe value composite (slow anchor, 20%).

Uses ONLY pre-computed _hist ratios from company_ratios:
  - book_to_price_hist   (already price-adjusted, no book_value hack)
  - earnings_to_price_hist
  - cashflow_to_price_hist

FIX (Codex audit #3): NEVER computes B/P from raw book_value / price.
FIX (Codex audit #2): company_ratios_wide is already PIT-lagged by data_loader.
"""

import pandas as pd
import numpy as np
from factors.z_scoring import sector_neutral_zscore


def compute_value(
    company_ratios_wide: pd.DataFrame,
    sectors: pd.Series,
    config: dict,
) -> pd.Series:
    """Compute value composite from _hist ratios only.

    Parameters
    ----------
    company_ratios_wide : PIT-lagged wide DataFrame from data_loader
    sectors : symbol -> GICS sector
    config : factors.value config block

    Returns
    -------
    pd.Series of sector-neutral z-scored value composite.
    """
    sub_w = config.get("sub_weights", {"bp": 0.33, "ep": 0.33, "cfp": 0.34})
    bp_weight = sub_w.get("bp", 0.33)
    ep_weight = sub_w.get("ep", 0.33)
    cfp_weight = sub_w.get("cfp", 0.34)

    bp = pd.Series(dtype=float, name="bp")
    ep = pd.Series(dtype=float, name="ep")
    cfp = pd.Series(dtype=float, name="cfp")

    if company_ratios_wide is None or company_ratios_wide.empty:
        return pd.Series(dtype=float, name="value")

    # Extract _hist ratios (the only acceptable source)
    if "book_to_price_hist" in company_ratios_wide.columns:
        bp_raw = company_ratios_wide["book_to_price_hist"]
        bp_raw = bp_raw.where(bp_raw.abs() < 10, np.nan)
        bp = bp_raw.rename("bp").replace([np.inf, -np.inf], np.nan)

    if "earnings_to_price_hist" in company_ratios_wide.columns:
        ep_raw = company_ratios_wide["earnings_to_price_hist"]
        ep_raw = ep_raw.where(ep_raw.abs() < 5, np.nan)
        ep = ep_raw.rename("ep").replace([np.inf, -np.inf], np.nan)

    if "cashflow_to_price_hist" in company_ratios_wide.columns:
        cfp_raw = company_ratios_wide["cashflow_to_price_hist"]
        cfp_raw = cfp_raw.where(cfp_raw.abs() < 5, np.nan)
        cfp = cfp_raw.rename("cfp").replace([np.inf, -np.inf], np.nan)

    # Z-score within sector
    z_bp = sector_neutral_zscore(bp, sectors) if len(bp.dropna()) > 0 else bp
    z_ep = sector_neutral_zscore(ep, sectors) if len(ep.dropna()) > 0 else ep
    z_cfp = sector_neutral_zscore(cfp, sectors) if len(cfp.dropna()) > 0 else cfp

    # Combine: NaN-safe weighted mean
    combined = pd.DataFrame({"z_bp": z_bp, "z_ep": z_ep, "z_cfp": z_cfp})
    weights = pd.Series({"z_bp": bp_weight, "z_ep": ep_weight, "z_cfp": cfp_weight})

    def _weighted_mean(row):
        valid = row.dropna()
        if len(valid) == 0:
            return np.nan
        w = weights.reindex(valid.index)
        return (valid * w).sum() / w.sum()

    result = combined.apply(_weighted_mean, axis=1)
    result.name = "value"
    return result
