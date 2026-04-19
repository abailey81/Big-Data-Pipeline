"""
factors/z_scoring.py -- Sector-neutral z-score normalisation.

Winsorisation at 2.5th/97.5th percentile BEFORE z-scoring.
Sectors with < 5 stocks get neutral z-scores (0.0).
"""

import pandas as pd
import numpy as np


def sector_neutral_zscore(
    values: pd.Series,
    sectors: pd.Series,
    winsor_lo: float = 0.025,
    winsor_hi: float = 0.975,
    min_stocks_per_sector: int = 5,
) -> pd.Series:
    """Compute sector-neutral z-scores with proper winsorisation.

    1. Within each GICS sector, winsorise raw values at 2.5th/97.5th percentile
    2. Then standardise (z = (x - mu) / sigma)
    3. Sectors with fewer than min_stocks_per_sector get neutral z-score (0.0)
    """
    common = values.dropna().index.intersection(sectors.dropna().index)
    if len(common) == 0:
        return pd.Series(np.nan, index=values.index)

    v = values.reindex(common)
    s = sectors.reindex(common)

    df = pd.DataFrame({"val": v, "sector": s})

    def _winsorise_and_zscore(g):
        if len(g) < min_stocks_per_sector:
            return pd.Series(0.0, index=g.index)

        lo = g.quantile(winsor_lo)
        hi = g.quantile(winsor_hi)
        w = g.clip(lower=lo, upper=hi)

        mu = w.mean()
        sigma = w.std(ddof=1)
        if sigma == 0 or np.isnan(sigma):
            return pd.Series(0.0, index=g.index)
        return (w - mu) / sigma

    z = df.groupby("sector")["val"].transform(_winsorise_and_zscore)
    return z.reindex(values.index)
